#!/usr/bin/env python3
"""Durable, dirfd-relative storage for absence evidence.

The configured evidence root is opened one path component at a time with
O_DIRECTORY|O_NOFOLLOW.  Every subsequent lookup and mutation is relative to
that trusted directory descriptor.  The observer publishes checksum first and
JSON last while holding a durable lock; the gate holds the same lock while it
snapshots and verifies both files.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import pwd
import re
import secrets
import signal
import stat
import sys


MAX_EVIDENCE_BYTES = 1024 * 1024
SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
RESERVED_OUTPUT_SUFFIXES = (".sha256", ".observe.lock", ".log")


class EvidenceStoreError(Exception):
    """A fail-closed storage boundary violation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def owner_uid(owner: str) -> int:
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise EvidenceStoreError("unknown_expected_owner") from exc


def write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise EvidenceStoreError("short_write")
        view = view[written:]


class EvidenceStore:
    """One absence-evidence basename rooted at a verified directory fd."""

    def __init__(self, root: str, expected_owner: str, out: str) -> None:
        if os.environ.get("VC_EVIDENCE_STORE_TEST_NO_NOFOLLOW") == "1" or not all(
            hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
        ):
            raise EvidenceStoreError("o_nofollow_unavailable")
        self.uid = owner_uid(expected_owner)
        if os.geteuid() != self.uid:
            raise EvidenceStoreError("effective_uid_does_not_match_expected_owner")
        self.root = self._canonical_root(root)
        self.root_fd = self._open_root_chain(self.root)
        self.token = ""
        try:
            root_stat = os.fstat(self.root_fd)
            if not stat.S_ISDIR(root_stat.st_mode):
                raise EvidenceStoreError("evidence_root_not_directory")
            if root_stat.st_uid != self.uid:
                raise EvidenceStoreError("evidence_root_wrong_owner")
            if stat.S_IMODE(root_stat.st_mode) != 0o700:
                raise EvidenceStoreError("evidence_root_mode_must_be_0700")
            self.basename = self._validated_basename(out)
            self.checksum_name = self.basename + ".sha256"
            self.lock_name = self.basename + ".observe.lock"
        except Exception:
            os.close(self.root_fd)
            raise

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    @staticmethod
    def _canonical_root(root: str) -> str:
        if not root.startswith("/") or root == "/":
            raise EvidenceStoreError("evidence_root_must_be_absolute_private_directory")
        if root != os.path.normpath(root):
            raise EvidenceStoreError("evidence_root_noncanonical")
        return root

    @staticmethod
    def _open_root_chain(root: str) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            current = os.open("/", flags)
        except OSError as exc:
            raise EvidenceStoreError(f"evidence_root_open_errno_{exc.errno}") from exc
        try:
            for component in root.split("/")[1:]:
                if not component or component in (".", ".."):
                    raise EvidenceStoreError("evidence_root_noncanonical")
                try:
                    next_fd = os.open(component, flags, dir_fd=current)
                except OSError as exc:
                    reason = (
                        "evidence_root_symlink_component"
                        if exc.errno in (errno.ELOOP, errno.ENOTDIR)
                        else f"evidence_root_open_errno_{exc.errno}"
                    )
                    raise EvidenceStoreError(reason) from exc
                os.close(current)
                current = next_fd
            return current
        except Exception:
            os.close(current)
            raise

    def _validated_basename(self, out: str) -> str:
        basename = os.path.basename(out)
        if out != os.path.join(self.root, basename):
            raise EvidenceStoreError("out_must_be_direct_child_of_evidence_root")
        if not SAFE_BASENAME.fullmatch(basename) or basename in (".", ".."):
            raise EvidenceStoreError("unsafe_output_basename")
        # These names are derived from another output basename.  Allowing one
        # as an output would let its invalidation delete or replace that other
        # evidence pair's checksum/lock namespace.
        if basename.casefold().endswith(RESERVED_OUTPUT_SUFFIXES):
            raise EvidenceStoreError("reserved_output_basename_suffix")
        try:
            name_max = os.fpathconf(self.root_fd, "PC_NAME_MAX")
        except (OSError, ValueError):
            name_max = 255
        longest = f".{basename}.observe.{'0' * 32}.sha256.tmp"
        if len(longest.encode("utf-8")) > name_max:
            raise EvidenceStoreError("output_basename_too_long")
        return basename

    def _stat_entry(self, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise EvidenceStoreError(f"entry_stat_errno_{exc.errno}") from exc

    def _validate_regular(self, name: str, *, required: bool = False) -> bool:
        item = self._stat_entry(name)
        if item is None:
            if required:
                raise EvidenceStoreError("required_entry_missing")
            return False
        if not stat.S_ISREG(item.st_mode):
            raise EvidenceStoreError("entry_not_regular")
        if item.st_uid != self.uid:
            raise EvidenceStoreError("entry_wrong_owner")
        if stat.S_IMODE(item.st_mode) & 0o022:
            raise EvidenceStoreError("entry_group_or_other_writable")
        return True

    def _open_lock(self) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            lock_fd = os.open(self.lock_name, flags, dir_fd=self.root_fd)
        except OSError as exc:
            raise EvidenceStoreError("observation_lock_missing_or_unsafe") from exc
        lock_stat = os.fstat(lock_fd)
        if lock_stat.st_uid != self.uid or stat.S_IMODE(lock_stat.st_mode) != 0o700:
            os.close(lock_fd)
            raise EvidenceStoreError("observation_lock_owner_or_mode")
        return lock_fd

    def _read_lock_token(self, lock_fd: int) -> str:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            token_fd = os.open("owner", flags, dir_fd=lock_fd)
        except OSError as exc:
            raise EvidenceStoreError("observation_lock_token_missing") from exc
        try:
            before = os.fstat(token_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.uid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size != 33
            ):
                raise EvidenceStoreError("observation_lock_token_metadata")
            payload = os.read(token_fd, 64)
            after = os.fstat(token_fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise EvidenceStoreError("observation_lock_token_changed")
        finally:
            os.close(token_fd)
        try:
            token = payload.decode("ascii").removesuffix("\n")
        except UnicodeDecodeError as exc:
            raise EvidenceStoreError("observation_lock_token_encoding") from exc
        if not SAFE_TOKEN.fullmatch(token):
            raise EvidenceStoreError("observation_lock_token_invalid")
        return token

    def _verified_lock(self, token: str | None = None) -> tuple[int, str]:
        lock_fd = self._open_lock()
        try:
            stored = self._read_lock_token(lock_fd)
            expected = token or self.token
            if not expected or not secrets.compare_digest(stored, expected):
                raise EvidenceStoreError("observation_lock_token_mismatch")
            return lock_fd, stored
        except Exception:
            os.close(lock_fd)
            raise

    def _unlink_regular(self, name: str, *, missing_ok: bool = True) -> bool:
        present = self._validate_regular(name, required=not missing_ok)
        if not present:
            return False
        try:
            os.unlink(name, dir_fd=self.root_fd)
        except OSError as exc:
            raise EvidenceStoreError(f"entry_unlink_errno_{exc.errno}") from exc
        return True

    def _sync_root(self) -> None:
        os.fsync(self.root_fd)

    def validate(self) -> None:
        self._validate_regular(self.basename)
        self._validate_regular(self.checksum_name)
        lock = self._stat_entry(self.lock_name)
        if lock is not None and not stat.S_ISDIR(lock.st_mode):
            raise EvidenceStoreError("observation_lock_not_directory")

    def begin(self, *, invalidate: bool) -> str:
        self._validate_regular(self.basename)
        self._validate_regular(self.checksum_name)
        token = secrets.token_hex(16)
        try:
            os.mkdir(self.lock_name, 0o700, dir_fd=self.root_fd)
        except FileExistsError as exc:
            raise EvidenceStoreError("observation_already_in_progress") from exc
        except OSError as exc:
            raise EvidenceStoreError(f"observation_lock_create_errno_{exc.errno}") from exc
        self._sync_root()
        lock_fd = self._open_lock()
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
            token_fd = os.open("owner", flags, 0o600, dir_fd=lock_fd)
            try:
                write_all(token_fd, (token + "\n").encode("ascii"))
                os.fsync(token_fd)
            finally:
                os.close(token_fd)
            os.fsync(lock_fd)
        finally:
            os.close(lock_fd)
        self._sync_root()
        self.token = token
        if invalidate:
            self._unlink_regular(self.basename)
            self._unlink_regular(self.checksum_name)
            self._sync_root()
        return token

    def prepare(self, payload: bytes, token: str | None = None) -> str:
        if not payload or len(payload) > MAX_EVIDENCE_BYTES:
            raise EvidenceStoreError("evidence_size_invalid")
        lock_fd, stored = self._verified_lock(token)
        os.close(lock_fd)
        json_temp, checksum_temp = self._temp_names(stored)
        self._unlink_regular(json_temp)
        self._unlink_regular(checksum_temp)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        json_fd = os.open(json_temp, flags, 0o600, dir_fd=self.root_fd)
        try:
            write_all(json_fd, payload)
            os.fsync(json_fd)
        finally:
            os.close(json_fd)
        digest = hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n"
        checksum_fd = os.open(checksum_temp, flags, 0o600, dir_fd=self.root_fd)
        try:
            write_all(checksum_fd, digest)
            os.fsync(checksum_fd)
        finally:
            os.close(checksum_fd)
        self._sync_root()
        return stored

    def _temp_names(self, token: str) -> tuple[str, str]:
        if not SAFE_TOKEN.fullmatch(token):
            raise EvidenceStoreError("transaction_token_invalid")
        stem = f".{self.basename}.observe.{token}"
        return stem + ".json.tmp", stem + ".sha256.tmp"

    def commit_checksum(self, token: str | None = None) -> None:
        lock_fd, stored = self._verified_lock(token)
        os.close(lock_fd)
        _, checksum_temp = self._temp_names(stored)
        self._validate_regular(checksum_temp, required=True)
        if self._stat_entry(self.checksum_name) is not None:
            raise EvidenceStoreError("checksum_destination_exists")
        os.rename(
            checksum_temp,
            self.checksum_name,
            src_dir_fd=self.root_fd,
            dst_dir_fd=self.root_fd,
        )
        self._sync_root()

    def commit_json(self, token: str | None = None) -> None:
        lock_fd, stored = self._verified_lock(token)
        os.close(lock_fd)
        json_temp, _ = self._temp_names(stored)
        self._validate_regular(json_temp, required=True)
        self._validate_regular(self.checksum_name, required=True)
        if self._stat_entry(self.basename) is not None:
            raise EvidenceStoreError("json_destination_exists")
        os.rename(
            json_temp,
            self.basename,
            src_dir_fd=self.root_fd,
            dst_dir_fd=self.root_fd,
        )
        self._sync_root()

    def release(self, token: str | None = None) -> None:
        lock_fd, _ = self._verified_lock(token)
        try:
            os.unlink("owner", dir_fd=lock_fd)
            os.fsync(lock_fd)
        finally:
            os.close(lock_fd)
        os.rmdir(self.lock_name, dir_fd=self.root_fd)
        self._sync_root()
        self.token = ""

    def abort(self, token: str | None = None) -> None:
        lock_fd, stored = self._verified_lock(token)
        os.close(lock_fd)
        self._unlink_regular(self.basename)
        self._unlink_regular(self.checksum_name)
        self._sync_root()
        json_temp, checksum_temp = self._temp_names(stored)
        self._unlink_regular(json_temp)
        self._unlink_regular(checksum_temp)
        self._sync_root()
        self.release(stored)


def add_store_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--root", required=True)
    command.add_argument("--expected-owner", required=True)
    command.add_argument("--out", required=True)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser()
    commands = top.add_subparsers(dest="command", required=True)
    for name in ("validate", "collector-begin", "gate-acquire"):
        item = commands.add_parser(name)
        add_store_args(item)
    for name in (
        "collector-prepare",
        "collector-commit-checksum",
        "collector-commit-json",
        "collector-abort",
        "gate-release",
    ):
        item = commands.add_parser(name)
        add_store_args(item)
        item.add_argument("--token", required=True)
    finish = commands.add_parser("collector-finish")
    add_store_args(finish)
    finish.add_argument("--token", required=True)
    finish.add_argument("--consecutive", required=True)
    finish.add_argument("--finalized-advance", required=True)
    return top


def read_payload() -> bytes:
    payload = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    if not payload or len(payload) > MAX_EVIDENCE_BYTES:
        raise EvidenceStoreError("evidence_size_invalid")
    return payload


def run(args: argparse.Namespace) -> int:
    with EvidenceStore(args.root, args.expected_owner, args.out) as store:
        if args.command == "validate":
            store.validate()
        elif args.command == "collector-begin":
            print(store.begin(invalidate=True))
        elif args.command == "collector-prepare":
            print(store.prepare(read_payload(), args.token))
        elif args.command == "collector-commit-checksum":
            store.commit_checksum(args.token)
        elif args.command == "collector-commit-json":
            store.commit_json(args.token)
        elif args.command == "collector-abort":
            store.abort(args.token)
        elif args.command == "collector-finish":
            if not args.consecutive.isdigit() or not args.finalized_advance.isdigit():
                raise EvidenceStoreError("finish_summary_not_unsigned_decimal")
            # The observer execs this final command.  Once lock removal and its
            # root-directory fsync complete, the pair is durably committed; do
            # not re-enter a shell signal/cleanup window after that point.
            blocked = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
            if not hasattr(signal, "pthread_sigmask"):
                raise EvidenceStoreError("signal_mask_unavailable")
            signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
            store.release(args.token)
            print(
                "OBSERVE=ABSENCE_OBSERVED "
                f"consec={args.consecutive} "
                f"finalized_advance={args.finalized_advance} out={args.out}"
            )
            print(
                "주의: 이것은 보강 증거일 뿐 전환 허가가 아니다 — "
                "source fence 증거(RB-01 F1)가 별도로 필요하다."
            )
            print(
                "주의: 이 증거는 짧게 유효하다. 오래되면 observe-absence를 "
                "다시 실행해야 한다."
            )
            sys.stdout.flush()
        elif args.command == "gate-acquire":
            print(store.begin(invalidate=False))
        elif args.command == "gate-release":
            store.release(args.token)
        else:  # pragma: no cover - argparse prevents this.
            raise EvidenceStoreError("unknown_command")
    return 0


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise EvidenceStoreError(signal.Signals(signum).name.lower())

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        return run(parser().parse_args())
    except EvidenceStoreError as exc:
        print(f"EVIDENCE_STORE=FAIL reason={exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"EVIDENCE_STORE=FAIL reason=internal_{type(exc).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
