#!/usr/bin/env python3
"""Fail-closed offline evaluator for paginated protected-main check runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn


REQUIRED_CONTEXTS = ("docs-contract", "terraform-static")
TRANSIENT_STATUSES = {"queued", "in_progress"}
HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ContractError(ValueError):
    """Raised when API evidence cannot safely authorize the next step."""


def fail(message: str) -> NoReturn:
    print(f"BASELINE_CHECKS=FAIL reason={message}", file=sys.stderr)
    raise SystemExit(2)


def load_pages(path: Path) -> list[dict[str, object]]:
    if path.is_symlink() or not path.is_file():
        raise ContractError("input_not_regular_file")
    try:
        with path.open(encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("input_not_valid_json") from error
    if not isinstance(document, list) or not document:
        raise ContractError("input_not_paginated_page_array")
    pages: list[dict[str, object]] = []
    for page in document:
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            raise ContractError("page_schema")
        pages.append(page)
    return pages


def evaluate(
    pages: list[dict[str, object]], expected_head_sha: str
) -> tuple[str, int | None, int]:
    if HEAD_SHA_RE.fullmatch(expected_head_sha) is None:
        raise ContractError("expected_head_sha")

    required: list[dict[str, object]] = []
    for page in pages:
        check_runs = page["check_runs"]
        assert isinstance(check_runs, list)
        for raw_run in check_runs:
            if not isinstance(raw_run, dict) or not isinstance(raw_run.get("name"), str):
                raise ContractError("check_run_schema")
            if raw_run["name"] in REQUIRED_CONTEXTS:
                required.append(raw_run)

    names = [str(run["name"]) for run in required]
    if len(names) != len(set(names)):
        raise ContractError("duplicate_required_context")
    if any(name not in REQUIRED_CONTEXTS for name in names):
        raise ContractError("unexpected_required_context")

    app_ids: list[int] = []
    pending = False
    for run in required:
        if run.get("head_sha") != expected_head_sha:
            raise ContractError("wrong_head_sha")
        app = run.get("app")
        if not isinstance(app, dict) or app.get("slug") != "github-actions":
            raise ContractError("wrong_app_slug")
        app_id = app.get("id")
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
            raise ContractError("app_id_not_positive_integer")
        app_ids.append(app_id)

        status = run.get("status")
        conclusion = run.get("conclusion")
        if status in TRANSIENT_STATUSES:
            if conclusion is not None:
                raise ContractError("transient_check_has_conclusion")
            pending = True
        elif status == "completed":
            if conclusion != "success":
                raise ContractError("terminal_check_not_success")
        else:
            raise ContractError("unknown_check_status")

    if app_ids and len(set(app_ids)) != 1:
        raise ContractError("required_context_app_id_mismatch")

    if len(required) < len(REQUIRED_CONTEXTS) or pending:
        return "WAIT", None, len(required)
    if sorted(names) != sorted(REQUIRED_CONTEXTS):
        raise ContractError("required_context_set")
    return "PASS", app_ids[0], len(required)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--expected-head-sha", required=True)
    args = parser.parse_args()

    try:
        state, app_id, count = evaluate(
            load_pages(args.input), args.expected_head_sha
        )
    except ContractError as error:
        fail(str(error))

    payload: dict[str, object] = {
        "state": state,
        "required_check_count": count,
    }
    if app_id is not None:
        payload["app_id"] = app_id
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if state == "PASS" else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        os._exit(1)
