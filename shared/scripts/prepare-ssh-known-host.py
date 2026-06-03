#!/usr/bin/env python3
"""Stage one ED25519 SSH host key in a private, non-symlink file."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def fail(reason: str) -> "NoReturn":
    raise SystemExit(f"SSH_HOST_KEY_STAGED=FAIL reason={reason}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-keyscan-bin", default="ssh-keyscan")
    parser.add_argument("--host", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        fail("host_must_be_ip")
    if address.version != 4 or not address.is_global:
        fail("host_must_be_public_ipv4")
    if args.output.is_symlink() or (args.output.exists() and not args.output.is_file()):
        fail("invalid_output")
    if args.output.parent.is_symlink() or not args.output.parent.is_dir():
        fail("invalid_output_parent")
    parent_mode = stat.S_IMODE(args.output.parent.stat().st_mode)
    if parent_mode & 0o022:
        fail("output_parent_writable_by_others")

    result = subprocess.run(
        [args.ssh_keyscan_bin, "-T", "5", "-t", "ed25519", args.host],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        fail(f"ssh_keyscan_rc_{result.returncode}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) != 1:
        fail("expected_one_key")
    fields = lines[0].split()
    if len(fields) != 3 or fields[0] != args.host or fields[1] != "ssh-ed25519":
        fail("invalid_key_line")
    try:
        key_bytes = base64.b64decode(fields[2], validate=True)
    except ValueError:
        fail("invalid_key_base64")
    fingerprint = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("ascii").rstrip("=")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=str(args.output.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(lines[0] + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
        directory_fd = os.open(args.output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    print(
        f"SSH_HOST_KEY_STAGED=PASS host={args.host} type=ED25519 "
        f"fingerprint=SHA256:{fingerprint} file={args.output}"
    )


if __name__ == "__main__":
    main()
