from __future__ import annotations

import re
import unittest

from _cicd_lib import PR_WORKFLOW, WORKFLOWS, read


class TerraformPrContractTests(unittest.TestCase):
    def test_pr_workflow_exists_with_required_check_names(self) -> None:
        self.assertTrue(PR_WORKFLOW.is_file())
        workflow = read(PR_WORKFLOW)
        # ruleset required status check의 context 이름 두 개가 고정이다.
        self.assertIn("    name: terraform-static", workflow)
        self.assertIn("    name: docs-contract", workflow)

    def test_pr_workflow_is_unprivileged_static_validation_only(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertRegex(workflow, r"permissions:\n\s+contents: read")
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("configure-aws-credentials", workflow)
        self.assertNotIn("aws-actions/", workflow)
        self.assertNotIn("terraform plan", workflow)
        self.assertNotIn("terraform apply", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform fmt -check -recursive", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform init -backend=false -lockfile=readonly", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform validate", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform test", workflow)

    def test_static_checks_run_on_the_exact_pr_merge_or_main_push_sha(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertRegex(
            workflow,
            r"on:\n\s+pull_request:\n\s+push:\n\s+branches:\n\s+- main",
        )
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), 2)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)
        self.assertEqual(workflow.count("fetch-depth: 0"), 2)

    def test_edition_gate_guards_both_jobs_and_allows_the_first_skeleton_import(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertEqual(workflow.count('CI_TEST_EDITION="$(cat EDITION)"'), 2)
        self.assertEqual(workflow.count("RELEASE_EDITION_DRIFT=FAIL"), 4)
        self.assertEqual(workflow.count("EDITION_BASELINE_IMPORT=OK"), 2)
        self.assertEqual(workflow.count('git cat-file -e "${BASE_SHA}:EDITION"'), 2)
        self.assertEqual(workflow.count("github.event.before"), 2)
        self.assertEqual(workflow.count("CHECK_BASE=false"), 2)
        self.assertEqual(workflow.count("0000000000000000000000000000000000000000"), 2)

    def test_terraform_static_skips_cleanly_before_the_first_terraform_slice(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertIn("if test -f primary-aws/terraform/versions.tf; then", workflow)
        self.assertNotIn("if test -d primary-aws/terraform; then", workflow)
        self.assertIn(
            "TERRAFORM_STATIC=SKIP reason=terraform_module_not_imported", workflow
        )
        self.assertIn("TERRAFORM_STATIC=PASS", workflow)
        self.assertIn('test "$(cat .terraform-version)" = "1.15.8"', workflow)
        self.assertIn("terraform_version: 1.15.8", workflow)
        self.assertIn("terraform_wrapper: false", workflow)

    def test_contract_suites_run_from_the_reviewed_enabled_manifest(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertIn("tests/enabled.txt", workflow)
        self.assertIn("CONTRACT_SUITES=FAIL reason=missing_enabled_manifest", workflow)
        self.assertIn("CONTRACT_SUITES=FAIL reason=missing_suite", workflow)
        self.assertIn("CONTRACT_SUITES=FAIL reason=empty_enabled_manifest", workflow)
        self.assertIn('*.py) python3 "$suite" ;;', workflow)
        self.assertIn('*.sh) bash "$suite" ;;', workflow)
        self.assertIn("CONTRACT_SUITES=PASS", workflow)

    def test_docs_contract_checks_codeowners_render_and_readme(self) -> None:
        workflow = read(PR_WORKFLOW)
        self.assertIn("python3 tests/test_codeowners_render.py", workflow)
        self.assertIn('grep -Fq "GitHub Actions" README.md', workflow)
        self.assertIn('grep -Fq "OIDC PlanRole/ApplyRole" README.md', workflow)
        self.assertIn('grep -Fq "protected Environment" README.md', workflow)
        self.assertIn("DOCS_CONTRACT=PASS", workflow)

    def test_action_references_are_full_sha_pinned_in_every_workflow(self) -> None:
        for workflow_path in sorted(WORKFLOWS.glob("*.yml")):
            for reference in re.findall(r"^\s*uses:\s*([^\s]+)", read(workflow_path), re.MULTILINE):
                with self.subTest(workflow=workflow_path.name, action=reference):
                    self.assertRegex(reference, r"^[^@\s]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
