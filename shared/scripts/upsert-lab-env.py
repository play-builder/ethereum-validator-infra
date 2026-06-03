#!/usr/bin/env python3
"""Atomically replace-or-append one validated instructor-lab environment value."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


HEX = "[0-9A-Fa-f]"
GITHUB_USER = re.compile(r"^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$")
PATTERNS = {
    "GITHUB_ADMIN_ACCOUNT": GITHUB_USER,
    "GITHUB_OPERATOR1_ACCOUNT": GITHUB_USER,
    "GITHUB_OPERATOR2_ACCOUNT": GITHUB_USER,
    "GITHUB_SECURITY_ACCOUNT": GITHUB_USER,
    "TOPIC_ARN": re.compile(r"^arn:(?:aws|aws-us-gov|aws-cn):sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_.:/+=,@-]+$"),
    "INSTANCE_ID": re.compile(r"^i-[0-9a-f]{8}(?:[0-9a-f]{9})?$"),
    "STANDBY_INSTANCE_ID": re.compile(r"^i-[0-9a-f]{8}(?:[0-9a-f]{9})?$"),
    "STANDBY_SECURITY_GROUP_ID": re.compile(r"^sg-[0-9a-f]{8}(?:[0-9a-f]{9})?$"),
    "PARAM_PREFIX": re.compile(r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$"),
    "RECOVERY_KEY_ARN": re.compile(r"^arn:(?:aws|aws-us-gov|aws-cn):kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$"),
    "VALSTATE_VOL_ID": re.compile(r"^vol-[0-9a-f]{8}(?:[0-9a-f]{9})?$"),
    "PUBKEY": re.compile(rf"^0x{HEX}{{96}}$"),
    "DEPOSIT_TX": re.compile(rf"^0x{HEX}{{64}}$"),
    "HOODI_DEPOSIT_CONTRACT": re.compile(rf"^0x{HEX}{{40}}$"),
    "VALIDATOR_INDEX": re.compile(r"^(?:0|[1-9][0-9]*)$"),
    "WORKLOAD_ACCOUNT_ID": re.compile(r"^[0-9]{12}$"),
    "GITHUB_REPOSITORY": re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$"),
    "GITHUB_REPOSITORY_OWNER_ID": re.compile(r"^[1-9][0-9]*$"),
    "GITHUB_REPOSITORY_ID": re.compile(r"^[1-9][0-9]*$"),
}
ALLOWED = frozenset({"NODE_IP", "STANDBY_NODE_IP", "EXPLORER_URL", *PATTERNS})


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"LAB_ENV_UPSERT=FAIL reason={message}")


def validate(name: str, value: str) -> None:
    if name not in ALLOWED:
        fail(f"variable_not_allowed name={name}")
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        fail(f"invalid_control_or_empty name={name}")
    if name in {"NODE_IP", "STANDBY_NODE_IP"}:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            fail("invalid_NODE_IP")
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            fail("invalid_NODE_IP")
        return
    if name == "EXPLORER_URL":
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            fail("invalid_EXPLORER_URL")
        return
    if not PATTERNS[name].fullmatch(value):
        fail(f"invalid_{name}")


def upsert(path: Path, name: str, value: str) -> None:
    validate(name, value)
    if path.is_symlink():
        fail("lab_env_symlink_rejected")
    if path.exists() and not path.is_file():
        fail("lab_env_not_regular")
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines(keepends=True)
    pattern = re.compile(rf"^export {re.escape(name)}=")
    replacement = f"export {name}={shlex.quote(value)}\n"
    output = []
    inserted = False
    for line in lines:
        if pattern.match(line):
            if not inserted:
                output.append(replacement)
                inserted = True
            continue
        output.append(line)
    if not inserted:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append(replacement)
    new_text = "".join(output)

    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, old_mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(new_text)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("name")
    parser.add_argument("value")
    args = parser.parse_args()
    upsert(args.file, args.name, args.value)
    print(f"LAB_ENV_UPSERT=OK name={args.name}")


if __name__ == "__main__":
    main()
