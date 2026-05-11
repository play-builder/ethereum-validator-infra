#!/usr/bin/env python3
"""Validate a reviewed client release lock and its derived site snapshots."""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import stat
import sys


sys.dont_write_bytecode = True

TOP_LEVEL_KEYS = {"schema_version", "network", "nethermind", "lighthouse", "approved_skew"}
SKEW_KEYS = {"enabled", "site", "change_record", "expires_at"}
NETHERMIND_KEYS = {
    "version", "build_commit", "archive_url", "sha256", "signature_url",
    "signature_sha256", "signer_fingerprint",
}
LIGHTHOUSE_KEYS = NETHERMIND_KEYS - {"build_commit"}
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{8,64}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9A-F]{40}$")


class ClientReleaseError(Exception):
    """A fail-closed client release contract error."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _no_duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_regular_json(path, not_regular_reason, invalid_reason, read_error_reason):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ClientReleaseError(not_regular_reason) from error
    except OSError as error:
        raise ClientReleaseError(read_error_reason) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ClientReleaseError(not_regular_reason)
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object), raw
    except OSError as error:
        raise ClientReleaseError(read_error_reason) from error
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ClientReleaseError(invalid_reason) from error


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_client_urls(name, client):
    version = client["version"]
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ClientReleaseError("LOCK_SCHEMA_INVALID")
    if name == "nethermind":
        build_commit = client["build_commit"]
        if not isinstance(build_commit, str) or not COMMIT_PATTERN.fullmatch(build_commit):
            raise ClientReleaseError("LOCK_SCHEMA_INVALID")
        archive_url = (
            "https://github.com/NethermindEth/nethermind/releases/download/"
            f"{version}/nethermind-{version}-{build_commit}-linux-x64.zip"
        )
    else:
        archive_url = (
            "https://github.com/sigp/lighthouse/releases/download/"
            f"v{version}/lighthouse-v{version}-x86_64-unknown-linux-gnu.tar.gz"
        )
    if client["archive_url"] != archive_url:
        raise ClientReleaseError("ARCHIVE_URL_NOT_PINNED")
    if client["signature_url"] != f"{archive_url}.asc":
        raise ClientReleaseError("SIGNATURE_URL_NOT_PINNED")


def _validate_skew(value):
    if not isinstance(value, dict) or set(value) != SKEW_KEYS or not isinstance(value["enabled"], bool):
        raise ClientReleaseError("LOCK_SCHEMA_INVALID")
    if not value["enabled"]:
        if any(value[key] is not None for key in ("site", "change_record", "expires_at")):
            raise ClientReleaseError("SKEW_NOT_APPROVED")
        return
    if value["site"] not in {"primary-aws", "standby-aws"} or not isinstance(value["change_record"], str) or not value["change_record"].strip() or not isinstance(value["expires_at"], str):
        raise ClientReleaseError("SKEW_NOT_APPROVED")
    try:
        expires_at = dt.datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError as error:
        raise ClientReleaseError("SKEW_NOT_APPROVED") from error
    if expires_at <= dt.datetime.now(dt.timezone.utc):
        raise ClientReleaseError("SKEW_EXPIRED")


def validate_lock(lock):
    if not isinstance(lock, dict) or set(lock) != TOP_LEVEL_KEYS:
        raise ClientReleaseError("LOCK_SCHEMA_INVALID")
    if type(lock["schema_version"]) is not int or lock["schema_version"] != 1:
        raise ClientReleaseError("LOCK_SCHEMA_INVALID")
    if not isinstance(lock["network"], str) or not lock["network"].strip():
        raise ClientReleaseError("LOCK_SCHEMA_INVALID")
    for client_name, expected_keys in (
        ("nethermind", NETHERMIND_KEYS),
        ("lighthouse", LIGHTHOUSE_KEYS),
    ):
        candidate = lock.get(client_name)
        if not isinstance(candidate, dict) or set(candidate) != expected_keys:
            raise ClientReleaseError("LOCK_SCHEMA_INVALID")
        if not _is_sha256(candidate["sha256"]) or not _is_sha256(candidate["signature_sha256"]):
            raise ClientReleaseError("SIGNATURE_SHA256_INVALID")
        if not isinstance(candidate["signer_fingerprint"], str) or not FINGERPRINT_PATTERN.fullmatch(candidate["signer_fingerprint"]):
            raise ClientReleaseError("SIGNER_FINGERPRINT_INVALID")
        _validate_client_urls(client_name, candidate)
    _validate_skew(lock["approved_skew"])


def load_and_validate_lock(path):
    lock, raw = read_regular_json(
        path, "LOCK_NOT_REGULAR", "LOCK_JSON_INVALID", "LOCK_READ_FAILED"
    )
    validate_lock(lock)
    return lock, raw


def validate_snapshot(path, lock, lock_sha256):
    snapshot, raw = read_regular_json(
        path, "SNAPSHOT_NOT_REGULAR", "SNAPSHOT_INVALID", "SNAPSHOT_READ_FAILED"
    )
    if not isinstance(snapshot, dict) or set(snapshot) != {"source_lock_sha256", "client_release_lock"}:
        raise ClientReleaseError("SNAPSHOT_INVALID")
    if snapshot["source_lock_sha256"] != lock_sha256 or snapshot["client_release_lock"] != lock:
        raise ClientReleaseError("SNAPSHOT_STALE")
    if raw.decode("utf-8") != canonical_json(snapshot):
        raise ClientReleaseError("SNAPSHOT_NOT_CANONICAL")
    return snapshot


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--primary-snapshot", required=True)
    parser.add_argument("--standby-snapshot", required=True)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    lock, raw_lock = load_and_validate_lock(arguments.lock)
    lock_sha256 = hashlib.sha256(raw_lock).hexdigest()
    primary = validate_snapshot(arguments.primary_snapshot, lock, lock_sha256)
    standby = validate_snapshot(arguments.standby_snapshot, lock, lock_sha256)
    if primary != standby:
        raise ClientReleaseError("SNAPSHOT_DIVERGED")
    print(canonical_json({"status": "PASS", "lock_sha256": lock_sha256}), end="")


if __name__ == "__main__":
    try:
        main()
    except ClientReleaseError as error:
        print(f"CLIENT_RELEASE_LOCK=FAIL reason={error.reason}", file=sys.stderr)
        raise SystemExit(1)
    except OSError:
        print("CLIENT_RELEASE_LOCK=FAIL reason=LOCK_IO_FAILED", file=sys.stderr)
        raise SystemExit(1)
