#!/usr/bin/env python3
"""Transport operator identities safely and bind them to current approvals."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


HEX = re.compile(r"^(?:[0-9a-f]{2})+$")


def die(reason: str) -> "NoReturn":
    raise SystemExit(f"OPERATOR_IDENTITY=FAIL reason={reason}")


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def checked_name(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value or not normalize(value):
        die("operator_name_invalid")
    return value


def decode(value: str) -> str:
    if not HEX.fullmatch(value):
        die("operator_hex_invalid")
    try:
        return checked_name(bytes.fromhex(value).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        die("operator_hex_invalid_utf8")


def regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        die(f"{label}_not_regular")
    return path


def token_operators(path: Path) -> list[str]:
    values: list[str] = []
    for line in regular(path, "token").read_text(encoding="utf-8").splitlines():
        if line.startswith("operators:"):
            if values:
                die("token_operators_duplicate")
            values = [checked_name(item.strip()) for item in line.split(":", 1)[1].split(",")]
    if not values:
        die("token_operators_missing")
    return values


def fence_operators(path: Path) -> list[str]:
    try:
        document = json.loads(regular(path, "source_fence").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("source_fence_json_invalid")
    values = document.get("operators") if isinstance(document, dict) else None
    if not isinstance(values, list) or not values or not all(isinstance(item, str) for item in values):
        die("source_fence_operators_invalid")
    return [checked_name(item) for item in values]


def normalized_set(values: list[str], label: str) -> set[str]:
    result = {normalize(value) for value in values}
    if "" in result or len(result) != len(values) or len(result) < 2:
        die(f"{label}_not_distinct")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("encode")
    encode.add_argument("--value", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--operator-hex", action="append", required=True)
    validate.add_argument("--token", required=True, type=Path)
    validate.add_argument("--source-fence", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "encode":
        print(checked_name(args.value).encode("utf-8").hex())
        return

    operators = [decode(value) for value in args.operator_hex]
    requested = normalized_set(operators, "requested_operators")
    token = normalized_set(token_operators(args.token), "token_operators")
    fence = normalized_set(fence_operators(args.source_fence), "source_fence_operators")
    if requested != token or requested != fence:
        die("operator_approval_set_mismatch")
    print(json.dumps(operators, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
