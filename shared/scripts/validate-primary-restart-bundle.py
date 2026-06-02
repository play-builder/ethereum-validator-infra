#!/usr/bin/env python3
"""Build and validate the human-approved primary restart evidence bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


SHA256 = re.compile(r"^[0-9a-f]{64}$")
INCIDENT = re.compile(r"^(?:CHG|INC)-[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
EXCLUDED = {"checklist.txt", "source-fence.json", "approval.token"}


def die(reason: str) -> "NoReturn":
    raise SystemExit(f"PRIMARY_RESTART_BUNDLE=FAIL reason={reason}")


def regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        die(f"{label}_not_regular")
    return path


def evidence_root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        die("evidence_dir_not_regular_directory")
    resolved = path.resolve()
    if not INCIDENT.fullmatch(resolved.name):
        die("evidence_dir_name_not_incident")
    return resolved


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_operator(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        die(f"{label}_not_canonical_utc")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        die(f"{label}_invalid_calendar")


def base_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if child.name in EXCLUDED or child.name.startswith(".checklist."):
            continue
        if child.is_symlink() or not child.is_file():
            die(f"non_regular_evidence name={child.name}")
        files.append(child)
    if not files:
        die("evidence_set_empty")
    return files


def checklist_bytes(root: Path) -> bytes:
    return "".join(f"{sha256(path)}  ./{path.name}\n" for path in base_files(root)).encode()


def validate_base(root: Path, incident_id: str) -> None:
    provider = regular(root / "provider-fence.txt", "provider_fence").read_text(encoding="utf-8")
    provider_fields: dict[str, str] = {}
    for line in provider.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            die("provider_fence_line_malformed")
        key, value = line.split("=", 1)
        if key in provider_fields:
            die(f"provider_fence_duplicate key={key}")
        provider_fields[key] = value
    if provider_fields.get("source_host_id") != "aws-standby-01":
        die("provider_source_host_mismatch")
    if provider_fields.get("fence_type") not in {"provider-stopped", "host-power-off"}:
        die("provider_fence_type_not_hard")
    if provider_fields.get("provider_state") != "fenced":
        die("provider_state_not_fenced")

    absence_path = regular(root / "current-absence.json", "absence")
    checksum_path = regular(root / "current-absence.json.sha256", "absence_checksum")
    checksum = checksum_path.read_text(encoding="utf-8").strip().split()[0]
    if not SHA256.fullmatch(checksum) or checksum != sha256(absence_path):
        die("absence_checksum_mismatch")
    try:
        absence = json.loads(absence_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("absence_json_invalid")
    if not isinstance(absence, dict):
        die("absence_not_object")
    if absence.get("schema") != "absence-evidence/v1" or absence.get("result") != "ABSENCE_OBSERVED":
        die("absence_result_invalid")
    if absence.get("incident") != incident_id:
        die("absence_incident_mismatch")


def atomic_write(path: Path, content: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_checklist(root: Path) -> None:
    validate_base(root, root.name)
    content = checklist_bytes(root)
    atomic_write(root / "checklist.txt", content)
    print(f"PRIMARY_RESTART_CHECKLIST=PASS sha256={hashlib.sha256(content).hexdigest()}")


def token_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in regular(path, "approval_token").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            die("token_line_malformed")
        key, value = line.split(":", 1)
        if key in fields:
            die(f"token_duplicate key={key}")
        fields[key] = value.strip()
    return fields


def validate_bundle(root: Path, incident_id: str, host_id: str) -> None:
    if not INCIDENT.fullmatch(incident_id) or root.name != incident_id:
        die("incident_id_invalid_or_path_mismatch")
    validate_base(root, incident_id)
    checklist = regular(root / "checklist.txt", "checklist")
    expected = checklist_bytes(root)
    if checklist.read_bytes() != expected:
        die("checklist_exact_bytes_mismatch")
    checklist_digest = sha256(checklist)

    fence_path = regular(root / "source-fence.json", "source_fence")
    try:
        fence = json.loads(fence_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("source_fence_json_invalid")
    if not isinstance(fence, dict) or fence.get("schema") != "source-fence-evidence/v2":
        die("source_fence_schema_invalid")
    required = {
        "network": "hoodi",
        "target_scope": "vc-start-primary",
        "source_host_id": "aws-standby-01",
        "incident_id": incident_id,
        "checklist_sha256": checklist_digest,
        "provider_state": "fenced",
        "vc_process_state": "absent",
    }
    if any(fence.get(key) != value for key, value in required.items()):
        die("source_fence_binding_mismatch")
    if fence.get("fence_type") not in {"provider-stopped", "host-power-off"}:
        die("source_fence_type_not_hard")
    fence_operators = fence.get("operators")
    if not isinstance(fence_operators, list) or not all(isinstance(item, str) for item in fence_operators):
        die("source_fence_operators_invalid")
    normalized_fence = {normalize_operator(item) for item in fence_operators}
    if "" in normalized_fence or len(normalized_fence) < 2:
        die("source_fence_operators_not_distinct")
    fenced_at = parse_utc(fence.get("fenced_at_utc"), "fenced_at")
    checked_at = parse_utc(fence.get("checked_at_utc"), "checked_at")
    if fenced_at > checked_at:
        die("source_fence_time_order_invalid")
    if not isinstance(fence.get("evidence_ref"), str) or not fence["evidence_ref"].strip():
        die("source_fence_evidence_ref_missing")

    token = token_fields(root / "approval.token")
    expected_token = {
        "token_version": "2",
        "host_id": host_id,
        "network": "hoodi",
        "scope": "vc-start-primary",
        "incident_id": incident_id,
        "checklist_sha256": checklist_digest,
        "source_fence_sha256": sha256(fence_path),
    }
    if any(token.get(key) != value for key, value in expected_token.items()):
        die("token_binding_mismatch")
    token_operators = {normalize_operator(item) for item in token.get("operators", "").split(",")}
    if "" in token_operators or token_operators != normalized_fence:
        die("token_operator_set_mismatch")
    issued = parse_utc(token.get("issued_at_utc"), "token_issued")
    expires = parse_utc(token.get("expires_at_utc"), "token_expires")
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > 168 * 3600:
        die("token_lifetime_invalid")
    print(
        "PRIMARY_RESTART_BUNDLE=PASS "
        f"incident={incident_id} checklist_sha256={checklist_digest} "
        f"source_fence_sha256={sha256(fence_path)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-checklist")
    build.add_argument("--evidence-dir", required=True, type=Path)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence-dir", required=True, type=Path)
    validate.add_argument("--incident-id", required=True)
    validate.add_argument("--host-id", required=True)
    args = parser.parse_args()
    root = evidence_root(args.evidence_dir)
    if args.command == "build-checklist":
        build_checklist(root)
    else:
        validate_bundle(root, args.incident_id, args.host_id)


if __name__ == "__main__":
    main()
