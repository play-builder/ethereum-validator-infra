#!/usr/bin/env python3
"""Contracts for student-copyable repository templates."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_EXAMPLE = ROOT / "primary-aws/terraform/ci/runtime-inputs.example.json"
LAB_ENV_EXAMPLE = ROOT / "lab.env.example"
CODEOWNERS_TEMPLATE = ROOT / "shared/templates/CODEOWNERS"


def placeholder_values(value: object) -> set[str]:
    if isinstance(value, dict):
        found: set[str] = set()
        for child in value.values():
            found.update(placeholder_values(child))
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found.update(placeholder_values(child))
        return found
    if isinstance(value, str) and "REPLACE_" in value:
        return {value}
    return set()


class StudentTemplateTests(unittest.TestCase):
    def test_runtime_example_requires_all_student_authority_values(self) -> None:
        payload = json.loads(RUNTIME_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(
            placeholder_values(payload),
            {
                "REPLACE_WITH_GITHUB_ORG/ethereum-validator-infra",
                "REPLACE_WITH_GITHUB_OWNER_ID",
                "REPLACE_WITH_GITHUB_REPOSITORY_ID",
                "REPLACE_WITH_AWS_ACCOUNT_ID",
                "REPLACE_WITH_TERRAFORM_PLAN_ROLE_ARN",
                "REPLACE_WITH_TERRAFORM_APPLY_ROLE_ARN",
                "REPLACE_WITH_KMS_BREAK_GLASS_ROLE_ARN",
                "REPLACE_WITH_STATE_BUCKET",
                "REPLACE_WITH_STATE_KMS_KEY_ARN",
                "REPLACE_WITH_PLAN_ARTIFACT_BUCKET",
                "REPLACE_WITH_PLAN_ARTIFACT_KMS_KEY_ARN",
                "REPLACE_WITH_NODE_PERMISSIONS_BOUNDARY_ARN",
                "REPLACE_WITH_GLOBAL_IPV4/32",
                "REPLACE_WITH_OPERATOR1_EMAIL",
                "REPLACE_WITH_OPERATOR2_EMAIL",
            },
        )

    def test_lab_env_documents_the_short_github_role_suffixes(self) -> None:
        body = LAB_ENV_EXAMPLE.read_text(encoding="utf-8")
        for suffix in ("-op1", "-op2", "-sec"):
            self.assertIn(f"${{GITHUB_ADMIN_ACCOUNT}}{suffix}", body)
        for stale in ("-operator-1-gh", "-operator-2-gh", "-security-approver-gh"):
            self.assertNotIn(stale, body)

    def test_codeowners_template_has_no_instructor_organization(self) -> None:
        body = CODEOWNERS_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(body.count("@YOUR_GITHUB_ORG/platform-approvers"), 5)
        self.assertEqual(body.count("@YOUR_GITHUB_ORG/security-approvers"), 7)
        self.assertNotIn("@play-builder/", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

