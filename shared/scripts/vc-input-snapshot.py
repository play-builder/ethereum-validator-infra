#!/usr/bin/env python3
"""Create and remove private, immutable vc-gate input snapshots.

The gate must hash and parse the same bytes. This helper pins every text/JSON
authorization input plus the SP metadata and stopped SP DB with O_NOFOLLOW,
validates their fstat metadata, then copies the small inputs and a pinned-FD DB
digest into a private directory before returning its path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import signal
import stat
import sys
import tempfile
import time
import unicodedata
from pathlib import Path


MAX_INPUT_BYTES = 1024 * 1024
SNAPSHOT_PREFIX = "vc-gate."
DESTINATIONS = {
    "TOKEN_FILE": "approval.token",
    "FENCE_EVIDENCE": "source-fence.json",
    "ABSENCE_EVIDENCE": "current-absence.json",
    "ABSENCE_CHECKSUM": "current-absence.json.sha256",
    "CHECKLIST_FILE": "checklist.txt",
    "PUBKEYS_FILE": "expected-pubkeys.txt",
    "SP_IMPORT_MARKER": "sp-import-approved",
}
SP_DB_DIGEST_DESTINATION = "slashing-protection.sqlite.sha256"


class SnapshotError(Exception):
    def __init__(self, input_name: str, reason: str) -> None:
        super().__init__(reason)
        self.input_name = input_name
        self.reason = reason


def expected_uid(owner: str) -> int:
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise SnapshotError("OWNER", "unknown_expected_owner") from exc


def validate_private_dir(path: Path, uid: int, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SnapshotError("BASE_DIR", f"create_failed_errno_{exc.errno}") from exc
    try:
        st = path.lstat()
    except OSError as exc:
        raise SnapshotError("BASE_DIR", f"stat_failed_errno_{exc.errno}") from exc
    if not stat.S_ISDIR(st.st_mode) or path.is_symlink():
        raise SnapshotError("BASE_DIR", "not_private_directory")
    if st.st_uid != uid:
        raise SnapshotError("BASE_DIR", "wrong_owner")
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise SnapshotError("BASE_DIR", "mode_must_be_0700")


def metadata_tuple(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_uid,
        st.st_mode,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def open_validated(
    name: str,
    path: str,
    uid: int,
    *,
    enforce_size_limit: bool = True,
    require_nonempty: bool = False,
) -> tuple[int, os.stat_result]:
    if os.environ.get("VC_INPUT_SNAPSHOT_TEST_NO_NOFOLLOW") == "1" or not hasattr(
        os, "O_NOFOLLOW"
    ):
        raise SnapshotError(name, "o_nofollow_unavailable")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        reason = "symlink_or_open_failed" if exc.errno in (40, 62) else f"open_failed_errno_{exc.errno}"
        raise SnapshotError(name, reason) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SnapshotError(name, "not_regular_file")
        if st.st_uid != uid:
            raise SnapshotError(name, "wrong_owner")
        if stat.S_IMODE(st.st_mode) & 0o022:
            raise SnapshotError(name, "group_or_other_writable")
        if name in ("TOKEN_FILE", "SP_IMPORT_MARKER") \
                and stat.S_IMODE(st.st_mode) not in (0o400, 0o600):
            raise SnapshotError(name, "mode_must_be_0400_or_0600")
        if require_nonempty and st.st_size == 0:
            raise SnapshotError(name, "empty_file")
        if enforce_size_limit and st.st_size > MAX_INPUT_BYTES:
            raise SnapshotError(name, "input_too_large")
        return fd, st
    except Exception:
        os.close(fd)
        raise


def unlink_known(snapshot_dir: Path) -> None:
    for filename in (*DESTINATIONS.values(), SP_DB_DIGEST_DESTINATION):
        try:
            (snapshot_dir / filename).unlink()
        except FileNotFoundError:
            pass
    try:
        snapshot_dir.rmdir()
    except FileNotFoundError:
        pass


def copy_pinned_fd(
    name: str, fd: int, before: os.stat_result, destination: Path
) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise SnapshotError(name, "o_nofollow_unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    out_fd = os.open(destination, flags, 0o600)
    total = 0
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise SnapshotError(name, "input_too_large")
            view = memoryview(chunk)
            while view:
                written = os.write(out_fd, view)
                view = view[written:]
        after = os.fstat(fd)
        if metadata_tuple(before) != metadata_tuple(after) or total != before.st_size:
            raise SnapshotError(name, "source_changed_while_snapshotting")
        os.fsync(out_fd)
        os.fchmod(out_fd, 0o400)
    finally:
        os.close(out_fd)


def hash_pinned_fd(
    name: str, fd: int, before: os.stat_result
) -> str:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    after = os.fstat(fd)
    if metadata_tuple(before) != metadata_tuple(after) or total != before.st_size:
        raise SnapshotError(name, "source_changed_while_hashing")
    return digest.hexdigest()


def write_private_file(name: str, destination: Path, payload: bytes) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise SnapshotError(name, "o_nofollow_unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(destination, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)


def wait_test_barrier(
    ready_path: str, continue_path: str, uid: int, snapshot_dir: Path
) -> None:
    ready = Path(ready_path)
    proceed = Path(continue_path)
    validate_private_dir(ready.parent, uid, create=False)
    if ready.parent != proceed.parent:
        raise SnapshotError("TEST_BARRIER", "marker_parents_differ")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SnapshotError("TEST_BARRIER", "o_nofollow_unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(ready, flags, 0o600)
        try:
            payload = f"{snapshot_dir}\n".encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise SnapshotError("TEST_BARRIER", f"ready_create_failed_errno_{exc.errno}") from exc
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            st = proceed.lstat()
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if not stat.S_ISREG(st.st_mode) or proceed.is_symlink():
            raise SnapshotError("TEST_BARRIER", "continue_not_regular")
        if st.st_uid != uid or stat.S_IMODE(st.st_mode) & 0o022:
            raise SnapshotError("TEST_BARRIER", "continue_permissions")
        return
    raise SnapshotError("TEST_BARRIER", "continue_timeout")


def create_snapshot(args: argparse.Namespace) -> int:
    uid = expected_uid(args.expected_owner)
    if os.geteuid() != uid:
        raise SnapshotError("OWNER", "effective_uid_does_not_match_expected_owner")
    base = Path(args.base_dir)
    validate_private_dir(base, uid, create=True)
    snapshot_dir: Path | None = None
    opened: dict[str, tuple[int, os.stat_result]] = {}
    sources = {
        "TOKEN_FILE": args.token,
        "FENCE_EVIDENCE": args.fence,
        "ABSENCE_EVIDENCE": args.absence,
        "ABSENCE_CHECKSUM": args.absence_checksum,
        "CHECKLIST_FILE": args.checklist,
        "PUBKEYS_FILE": args.pubkeys,
        "SP_IMPORT_MARKER": args.sp_marker,
    }
    try:
        for name, source in sources.items():
            source_uid = expected_uid(args.sp_marker_owner) \
                if name == "SP_IMPORT_MARKER" else uid
            opened[name] = open_validated(
                name,
                source,
                source_uid,
                require_nonempty=name == "SP_IMPORT_MARKER",
            )
        opened["SP_DB"] = open_validated(
            "SP_DB",
            args.sp_db,
            expected_uid(args.sp_db_owner),
            enforce_size_limit=False,
            require_nonempty=True,
        )
        snapshot_dir = Path(tempfile.mkdtemp(prefix=SNAPSHOT_PREFIX, dir=base))
        os.chmod(snapshot_dir, 0o700)
        if bool(args.test_open_ready_file) != bool(args.test_open_continue_file):
            raise SnapshotError("TEST_BARRIER", "both_open_markers_required")
        if args.test_open_ready_file:
            wait_test_barrier(
                args.test_open_ready_file,
                args.test_open_continue_file,
                uid,
                snapshot_dir,
            )
        for name in DESTINATIONS:
            fd, before = opened[name]
            copy_pinned_fd(name, fd, before, snapshot_dir / DESTINATIONS[name])
        sp_db_fd, sp_db_before = opened["SP_DB"]
        sp_db_sha256 = hash_pinned_fd("SP_DB", sp_db_fd, sp_db_before)
        write_private_file(
            "SP_DB",
            snapshot_dir / SP_DB_DIGEST_DESTINATION,
            f"{sp_db_sha256}\n".encode("ascii"),
        )
        dir_fd = os.open(snapshot_dir, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        if bool(args.test_ready_file) != bool(args.test_continue_file):
            raise SnapshotError("TEST_BARRIER", "both_markers_required")
        if args.test_ready_file:
            wait_test_barrier(
                args.test_ready_file, args.test_continue_file, uid, snapshot_dir
            )
        print(snapshot_dir)
        snapshot_dir = None
        return 0
    finally:
        for fd, _ in opened.values():
            os.close(fd)
        if snapshot_dir is not None:
            unlink_known(snapshot_dir)


def cleanup_snapshot(args: argparse.Namespace) -> int:
    uid = expected_uid(args.expected_owner)
    base_path = Path(args.base_dir)
    validate_private_dir(base_path, uid, create=False)
    base = base_path.resolve(strict=True)
    target = Path(args.snapshot_dir)
    try:
        resolved_parent = target.parent.resolve(strict=True)
        st = target.lstat()
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_DIR", f"stat_failed_errno_{exc.errno}") from exc
    if resolved_parent != base or not target.name.startswith(SNAPSHOT_PREFIX):
        raise SnapshotError("SNAPSHOT_DIR", "outside_base_or_bad_name")
    if target.is_symlink() or not stat.S_ISDIR(st.st_mode):
        raise SnapshotError("SNAPSHOT_DIR", "not_directory")
    if st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o700:
        raise SnapshotError("SNAPSHOT_DIR", "wrong_owner_or_mode")
    unlink_known(target)
    return 0


def normalize_identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def identity_count_text(args: argparse.Namespace) -> int:
    identities = {normalize_identity(item) for item in args.value.split(",")}
    identities.discard("")
    print(len(identities))
    return 0


def identity_count_json(args: argparse.Namespace) -> int:
    try:
        with open(args.file, "r", encoding="utf-8") as stream:
            document = json.load(stream)
        operators = document["operators"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise SnapshotError("OPERATORS", "invalid_json") from exc
    if not isinstance(operators, list) or any(not isinstance(item, str) for item in operators):
        raise SnapshotError("OPERATORS", "must_be_string_array")
    identities = {normalize_identity(item) for item in operators}
    identities.discard("")
    print(len(identities))
    return 0


def json_uint_field(args: argparse.Namespace) -> int:
    """Print one top-level JSON integer only when it is canonical/shell-safe.

    Python's JSON decoder preserves the distinction between an integer token
    and float/exponent syntax, unlike a jq round-trip that can turn 3.0 into 3.
    """
    try:
        with open(args.file, "r", encoding="utf-8") as stream:
            document = json.load(stream)
        value = document[args.field]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise SnapshotError("JSON_UINT", "missing_or_invalid_json") from exc
    if type(value) is not int or value < 0 or value > 9223372036854775807:
        raise SnapshotError("JSON_UINT", "not_safe_unsigned_integer")
    print(value)
    return 0


def file_sha256(args: argparse.Namespace) -> int:
    uid = expected_uid(args.expected_owner)
    fd, before = open_validated(
        args.input_name,
        args.file,
        uid,
        enforce_size_limit=False,
        require_nonempty=args.require_nonempty,
    )
    try:
        print(hash_pinned_fd(args.input_name, fd, before))
    finally:
        os.close(fd)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--base-dir", required=True)
    create.add_argument("--expected-owner", required=True)
    create.add_argument("--token", required=True)
    create.add_argument("--fence", required=True)
    create.add_argument("--absence", required=True)
    create.add_argument("--absence-checksum", required=True)
    create.add_argument("--checklist", required=True)
    create.add_argument("--pubkeys", required=True)
    create.add_argument("--sp-marker", required=True)
    create.add_argument("--sp-marker-owner", required=True)
    create.add_argument("--sp-db", required=True)
    create.add_argument("--sp-db-owner", required=True)
    create.add_argument("--test-ready-file", default="")
    create.add_argument("--test-continue-file", default="")
    create.add_argument("--test-open-ready-file", default="")
    create.add_argument("--test-open-continue-file", default="")
    create.set_defaults(func=create_snapshot)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--base-dir", required=True)
    cleanup.add_argument("--expected-owner", required=True)
    cleanup.add_argument("--snapshot-dir", required=True)
    cleanup.set_defaults(func=cleanup_snapshot)

    text_count = sub.add_parser("identity-count-text")
    text_count.add_argument("--value", required=True)
    text_count.set_defaults(func=identity_count_text)

    json_count = sub.add_parser("identity-count-json")
    json_count.add_argument("--file", required=True)
    json_count.set_defaults(func=identity_count_json)

    uint_field = sub.add_parser("json-uint-field")
    uint_field.add_argument("--file", required=True)
    uint_field.add_argument("--field", required=True)
    uint_field.set_defaults(func=json_uint_field)

    digest = sub.add_parser("file-sha256")
    digest.add_argument("--file", required=True)
    digest.add_argument("--expected-owner", required=True)
    digest.add_argument("--input-name", required=True)
    digest.add_argument("--require-nonempty", action="store_true")
    digest.set_defaults(func=file_sha256)
    return top


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise SnapshotError("SIGNAL", signal.Signals(signum).name.lower())

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    args = parser().parse_args()
    try:
        return args.func(args)
    except SnapshotError as exc:
        print(
            f"SNAPSHOT=FAIL INPUT={exc.input_name} REASON={exc.reason}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # Fail closed without leaking source contents.
        print(
            f"SNAPSHOT=FAIL INPUT=INTERNAL REASON={type(exc).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
