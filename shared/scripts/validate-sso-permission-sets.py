#!/usr/bin/env python3
"""Validate the exact IAM Identity Center permission-set list in tfvars."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ASSIGNMENT = re.compile(
    r"(?ms)^\s*sso_operator_permission_sets\s*=\s*\[(?P<body>.*?)\]"
)
QUOTED_VALUE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
PERMISSION_SET_NAME = re.compile(r"^[A-Za-z0-9_+=,.@-]{1,32}$")


def fail(reason: str) -> "NoReturn":
    print(f"SSO_PERMISSION_SETS=FAIL reason={reason}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--expected", required=True, action="append")
    args = parser.parse_args()

    if args.file.is_symlink() or not args.file.is_file():
        fail("invalid_tfvars_file")
    if (
        len(args.expected) != 2
        or len(set(args.expected)) != 2
        or any(PERMISSION_SET_NAME.fullmatch(name) is None for name in args.expected)
    ):
        fail("invalid_expected_contract")

    text = args.file.read_text(encoding="utf-8")
    matches = list(ASSIGNMENT.finditer(text))
    if len(matches) != 1:
        fail("assignment_count")
    values = QUOTED_VALUE.findall(matches[0].group("body"))
    if (
        len(values) != 2
        or len(set(values)) != 2
        or any(PERMISSION_SET_NAME.fullmatch(name) is None for name in values)
    ):
        fail("value_count")
    if set(values) != set(args.expected):
        fail("unexpected_name")

    print("SSO_PERMISSION_SETS=PASS count=2")


if __name__ == "__main__":
    main()
