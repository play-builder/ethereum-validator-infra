#!/usr/bin/env python3
"""Render the verified GitHub organization into the packaged CODEOWNERS template."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


REPOSITORY = re.compile(
    r"^(?P<org>[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38}))/(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99}))$"
)
RULES = (
    ("/.github/workflows/", "platform-approvers"),
    ("/.github/CODEOWNERS", "security-approvers"),
    ("/primary-aws/terraform/", "platform-approvers"),
    ("/primary-aws/terraform/ci/runtime-inputs.json", "security-approvers"),
    ("/primary-aws/bootstrap/", "security-approvers"),
    ("/primary-aws/bootstrap/cicd/parameters.json", "security-approvers"),
    ("/primary-aws/ansible/", "platform-approvers"),
    ("/standby-aws/terraform/", "platform-approvers"),
    ("/standby-aws/terraform/ci/runtime-inputs.json", "security-approvers"),
    ("/standby-aws/ansible/", "platform-approvers"),
    ("/shared/", "security-approvers"),
    ("/drills/", "security-approvers"),
)
PLACEHOLDER = "YOUR_GITHUB_ORG"


def fail(reason: str) -> "NoReturn":
    print(f"CODEOWNERS_RENDER=FAIL reason={reason}", file=sys.stderr)
    raise SystemExit(1)


def parse_rules(text: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            fail("unexpected_content")
        parsed.append((fields[0], fields[1]))
    return parsed


def expected_rules(org: str) -> list[tuple[str, str]]:
    return [(path, f"@{org}/{team}") for path, team in RULES]


def atomic_replace(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
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
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()

    match = REPOSITORY.fullmatch(args.repository)
    if match is None or match.group("org") in {".", ".."}:
        fail("invalid_repository")
    org = match.group("org")

    if args.file.is_symlink():
        fail("symlink_rejected")
    if not args.file.is_file():
        fail("not_regular_file")

    original = args.file.read_text(encoding="utf-8")
    parsed = parse_rules(original)
    placeholder_rules = expected_rules(PLACEHOLDER)
    rendered_rules = expected_rules(org)
    if parsed == rendered_rules:
        print(f"CODEOWNERS_RENDER=OK org={org} changed=false")
        return
    if parsed != placeholder_rules:
        fail("unexpected_content")

    rendered = original.replace(PLACEHOLDER, org)
    if parse_rules(rendered) != rendered_rules or PLACEHOLDER in rendered:
        fail("post_render_validation")
    atomic_replace(args.file, rendered)
    print(f"CODEOWNERS_RENDER=OK org={org} changed=true")


if __name__ == "__main__":
    main()
