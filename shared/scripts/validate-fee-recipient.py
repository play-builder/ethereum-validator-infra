#!/usr/bin/env python3
"""Fail closed on malformed, zero, placeholder, or mismatched fee recipients."""

from __future__ import annotations

import argparse
import re


ADDRESS = re.compile(r"^0x[0-9A-Fa-f]{40}$")
ZERO = "0x" + "0" * 40


def validate(value: str, label: str) -> None:
    if not ADDRESS.fullmatch(value):
        raise SystemExit(f"FEE_RECIPIENT=FAIL reason={label}_malformed")
    if value.lower() == ZERO:
        raise SystemExit(f"FEE_RECIPIENT=FAIL reason={label}_zero")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--value", required=True)
    parser.add_argument("--expected")
    args = parser.parse_args()
    validate(args.value, "value")
    if args.expected is not None:
        validate(args.expected, "expected")
        if args.value.lower() != args.expected.lower():
            raise SystemExit("FEE_RECIPIENT=FAIL reason=value_mismatch")
    print(f"FEE_RECIPIENT=PASS value={args.value}")


if __name__ == "__main__":
    main()
