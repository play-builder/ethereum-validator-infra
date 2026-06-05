#!/usr/bin/env python3
"""Atomically initialize a user-owned lab file without overwriting progress."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def die(reason: str) -> "NoReturn":
    raise SystemExit(f"INIT_IF_ABSENT=FAIL reason={reason}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    if args.source.is_symlink() or not args.source.is_file():
        die("source_not_regular")
    if args.target.is_symlink():
        die("target_symlink_rejected")
    if args.target.exists():
        if not args.target.is_file():
            die("target_not_regular")
        print(f"INIT_IF_ABSENT=PRESERVED target={args.target}")
        return

    args.target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{args.target.name}.", dir=str(args.target.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with args.source.open("rb") as source, os.fdopen(fd, "wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, args.target)
        except FileExistsError:
            die("target_appeared_during_create")
        directory_fd = os.open(str(args.target.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    print(f"INIT_IF_ABSENT=CREATED target={args.target}")


if __name__ == "__main__":
    main()
