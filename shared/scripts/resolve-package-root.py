#!/usr/bin/env python3
"""Resolve an extracted instructor package from parent, root, or nested cwd."""

from __future__ import annotations

import argparse
from pathlib import Path


def is_package(path: Path) -> bool:
    return (
        (path / "lab.env.example").is_file()
        and (path / "primary-aws").is_dir()
        and (path / "shared").is_dir()
    )


def resolve(start: Path) -> Path:
    start = start.expanduser().resolve(strict=True)
    if not start.is_dir():
        raise SystemExit("PACKAGE_ROOT=FAIL reason=start_not_directory")
    direct_child = start / "ethereum-validator-infra"
    if is_package(start):
        return start
    if is_package(direct_child):
        return direct_child.resolve()
    for candidate in start.parents:
        if is_package(candidate):
            return candidate
    raise SystemExit(f"PACKAGE_ROOT=FAIL reason=not_found start={start}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=Path)
    args = parser.parse_args()
    print(resolve(args.start))


if __name__ == "__main__":
    main()
