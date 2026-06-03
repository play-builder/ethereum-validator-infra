#!/usr/bin/env python3
"""Create the primary Ansible inventory once, including its dedicated PEM."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path


def die(reason: str) -> "NoReturn":
    raise SystemExit(f"PRIMARY_INVENTORY=FAIL reason={reason}")


def expected_text(node_ip: str, key_file: Path) -> str:
    try:
        address = ipaddress.ip_address(node_ip)
    except ValueError:
        die("invalid_node_ip")
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        die("invalid_node_ip")
    if not key_file.is_absolute():
        die("key_file_must_be_absolute")
    return (
        "---\n"
        "all:\n"
        "  children:\n"
        "    primary_aws:\n"
        "      hosts:\n"
        "        aws-primary-01:\n"
        f"          ansible_host: {address}\n"
        "          ansible_user: ubuntu\n"
        f"          ansible_ssh_private_key_file: {json.dumps(str(key_file))}\n"
    )


def verify_existing(text: str, node_ip: str, key_file: Path) -> None:
    hosts = re.findall(r"^\s*ansible_host:\s*([^\s#]+)", text, re.MULTILINE)
    keys = re.findall(r'^\s*ansible_ssh_private_key_file:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if hosts != [node_ip]:
        die(f"existing_inventory_node_ip_mismatch expected={node_ip}")
    normalized_keys = [value.rstrip('"\'') for value in keys]
    if normalized_keys != [str(key_file)]:
        die("existing_inventory_key_file_mismatch")


def atomic_create(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            die("inventory_appeared_during_create")
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
    parser.add_argument("--inventory-file", required=True, type=Path)
    parser.add_argument("--node-ip", required=True)
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args()
    content = expected_text(args.node_ip, args.key_file)
    inventory_file = args.inventory_file
    if inventory_file.is_symlink():
        die("inventory_symlink_rejected")
    if inventory_file.exists():
        if not inventory_file.is_file():
            die("inventory_not_regular")
        verify_existing(inventory_file.read_text(encoding="utf-8"), args.node_ip, args.key_file)
        print(f"PRIMARY_INVENTORY=PRESERVED path={inventory_file}")
        return
    atomic_create(inventory_file, content)
    print(f"PRIMARY_INVENTORY=CREATED path={inventory_file}")


if __name__ == "__main__":
    main()
