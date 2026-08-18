#!/usr/bin/env python3
"""Contract tests for protected-main baseline check-run discovery."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "primary-aws/bootstrap/cicd/check-baseline-check-runs.py"
HEAD_SHA = "4d1f0d2a9b8c7e6f5a4b3c2d1e0f987654321abc"
APP_ID = 15368


def check_run(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    head_sha: str = HEAD_SHA,
    app_slug: str = "github-actions",
    app_id: object = APP_ID,
) -> dict[str, object]:
    return {
        "id": 100,
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app_slug, "id": app_id},
    }


class BaselineCheckRunContractTests(unittest.TestCase):
    def run_helper(self, pages: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "check-runs-pages.json"
            source.write_text(json.dumps(pages), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--input",
                    str(source),
                    "--expected-head-sha",
                    HEAD_SHA,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def assert_wait(self, pages: object, expected_count: int) -> None:
        result = self.run_helper(pages)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"state": "WAIT", "required_check_count": expected_count},
        )

    def assert_rejected(self, pages: object) -> None:
        result = self.run_helper(pages)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("BASELINE_CHECKS=FAIL", result.stderr)

    def test_zero_one_and_pending_required_checks_are_transient_wait_states(self) -> None:
        self.assert_wait([{"check_runs": []}], 0)
        self.assert_wait(
            [{"check_runs": [check_run("docs-contract", status="queued", conclusion=None)]}],
            1,
        )
        self.assert_wait(
            [
                {
                    "check_runs": [
                        check_run("docs-contract"),
                        check_run("terraform-static", status="in_progress", conclusion=None),
                    ]
                }
            ],
            2,
        )

    def test_exact_two_successful_checks_from_one_github_actions_app_pass(self) -> None:
        result = self.run_helper(
            [
                {"check_runs": [check_run("docs-contract")]},
                {"check_runs": [check_run("terraform-static")]},
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "app_id": APP_ID,
                "required_check_count": 2,
                "state": "PASS",
            },
        )

    def test_terminal_wrong_identity_duplicate_and_unknown_states_fail_closed(self) -> None:
        valid = [
            check_run("docs-contract"),
            check_run("terraform-static"),
        ]
        terminal = [
            check_run("docs-contract", conclusion="failure"),
            check_run("terraform-static"),
        ]
        wrong_head = [
            check_run("docs-contract", head_sha="f" * 40),
            check_run("terraform-static"),
        ]
        wrong_app = [
            check_run("docs-contract", app_slug="external-ci", app_id=999),
            check_run("terraform-static"),
        ]
        mismatched_app_id = [
            check_run("docs-contract"),
            check_run("terraform-static", app_id=APP_ID + 1),
        ]
        unknown_status = [
            check_run("docs-contract", status="waiting", conclusion=None),
            check_run("terraform-static"),
        ]
        non_numeric_app = [
            check_run("docs-contract", app_id=str(APP_ID)),
            check_run("terraform-static", app_id=str(APP_ID)),
        ]

        for fixture in (
            terminal,
            wrong_head,
            wrong_app,
            mismatched_app_id,
            unknown_status,
            non_numeric_app,
        ):
            with self.subTest(fixture=fixture):
                self.assert_rejected([{"check_runs": fixture}])

        # A matching duplicate hidden on a later API page must not be accepted.
        self.assert_rejected(
            [
                {"check_runs": valid},
                {"check_runs": [check_run("docs-contract")]},
            ]
        )

    def test_malformed_or_non_paginated_api_payload_fails_closed(self) -> None:
        for fixture in (
            {"check_runs": []},
            [],
            [{"check_runs": "not-a-list"}],
            [{"check_runs": [{}]}],
        ):
            with self.subTest(fixture=fixture):
                self.assert_rejected(fixture)

if __name__ == "__main__":
    unittest.main()
