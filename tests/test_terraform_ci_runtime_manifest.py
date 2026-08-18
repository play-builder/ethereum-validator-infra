#!/usr/bin/env python3
"""Behavior tests for the tracked Terraform CI runtime manifest compiler."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "shared/scripts/render-terraform-ci-runtime.py"


VALID_MANIFEST = {
    "schema_version": 1,
    "repository": "play-builder/ethereum-validator-infra",
    "repository_owner_id": "1234567",
    "repository_id": "987654321",
    "environment": "hoodi-testnet-dev",
    "aws": {
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "state_key": "hoodi-testnet-dev/terraform.tfstate",
        "plan_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-TerraformPlanRole",
        "apply_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-TerraformApplyRole",
        "kms_break_glass_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-KmsBreakGlassRole",
        "state_bucket": "hoodi-testnet-dev-tfstate-123456789012",
        "state_kms_key_arn": "arn:aws:kms:ap-northeast-2:123456789012:key/11111111-2222-4333-8444-555555555555",
        "plan_artifact_bucket": "hoodi-testnet-dev-tfplan-123456789012",
        "plan_artifact_kms_key_arn": "arn:aws:kms:ap-northeast-2:123456789012:key/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "node_permissions_boundary_arn": "arn:aws:iam::123456789012:policy/hoodi-testnet-dev-node-permissions-boundary",
    },
    "terraform": {
        "network": "hoodi",
        "region": "ap-northeast-2",
        "kms_recovery_region": "eu-central-1",
        "key_pair_name": "eth-failover-hoodi",
        "admin_cidrs": ["8.8.8.8/32", "1.1.1.1/32"],
        "backup_peer_public_ip": None,
        "sso_operator_permission_sets": [
            "testnet_operator_01_builder",
            "testnet_operator_02_approver",
        ],
        "operator_alert_emails": [
            "playbuilder47+testnet_op1@gmail.com",
            "playbuilder47+testnet_op2@gmail.com",
        ],
        "enable_deadman_alarm": False,
        "enable_staging_bucket": True,
    },
}


class TerraformCiRuntimeManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "runtime-inputs.json"
        self.output = self.root / "rendered"
        self.write_manifest(VALID_MANIFEST)

    def write_manifest(self, payload: object) -> None:
        self.manifest.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def run_helper(
        self,
        mode: str = "deploy",
        *,
        repository: str = "play-builder/ethereum-validator-infra",
        owner_id: str = "1234567",
        repository_id: str = "987654321",
        environment: str = "hoodi-testnet-dev",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(HELPER),
                "--manifest",
                str(self.manifest),
                "--mode",
                mode,
                "--output-dir",
                str(self.output),
                "--expected-repository",
                repository,
                "--expected-owner-id",
                owner_id,
                "--expected-repository-id",
                repository_id,
                "--expected-environment",
                environment,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_failed_without_outputs(
        self, result: subprocess.CompletedProcess[str], reason: str
    ) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"reason={reason}", result.stderr)
        self.assertFalse(self.output.exists(), "failed validation must publish nothing")

    def test_deploy_renders_deterministic_non_secret_artifacts_and_machine_output(self) -> None:
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)

        canonical_path = self.output / "runtime-inputs.canonical.json"
        tfvars_path = self.output / "ci.auto.tfvars.json"
        backend_path = self.output / "backend.hcl"
        control_path = self.output / "control.json"
        expected_canonical = (
            json.dumps(
                VALID_MANIFEST,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode()
        self.assertEqual(canonical_path.read_bytes(), expected_canonical)

        tfvars = json.loads(tfvars_path.read_text(encoding="utf-8"))
        self.assertEqual(
            tfvars,
            {
                "admin_cidrs": ["8.8.8.8/32", "1.1.1.1/32"],
                "allow_protected_destroy": False,
                "backup_peer_public_ip": None,
                "enable_deadman_alarm": False,
                "enable_staging_bucket": True,
                "key_pair_name": "eth-failover-hoodi",
                "kms_recovery_region": "eu-central-1",
                "kms_break_glass_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-KmsBreakGlassRole",
                "network": "hoodi",
                "node_permissions_boundary_arn": "arn:aws:iam::123456789012:policy/hoodi-testnet-dev-node-permissions-boundary",
                "operator_alert_emails": [
                    "playbuilder47+testnet_op1@gmail.com",
                    "playbuilder47+testnet_op2@gmail.com",
                ],
                "region": "ap-northeast-2",
                "sso_operator_permission_sets": [
                    "testnet_operator_01_builder",
                    "testnet_operator_02_approver",
                ],
                "terraform_apply_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-TerraformApplyRole",
                "terraform_plan_role_arn": "arn:aws:iam::123456789012:role/hoodi-testnet-dev-TerraformPlanRole",
            },
        )
        self.assertEqual(
            backend_path.read_text(encoding="utf-8"),
            'bucket       = "hoodi-testnet-dev-tfstate-123456789012"\n'
            'key          = "hoodi-testnet-dev/terraform.tfstate"\n'
            'region       = "ap-northeast-2"\n'
            "encrypt      = true\n"
            'kms_key_id   = "arn:aws:kms:ap-northeast-2:123456789012:key/11111111-2222-4333-8444-555555555555"\n'
            "use_lockfile = true\n",
        )

        manifest_sha = hashlib.sha256(expected_canonical).hexdigest()
        control = json.loads(control_path.read_text(encoding="utf-8"))
        self.assertEqual(control["mode"], "deploy")
        self.assertEqual(control["repository_owner_id"], "1234567")
        self.assertEqual(control["repository_id"], "987654321")
        self.assertEqual(control["aws_account_id"], "123456789012")
        self.assertEqual(control["aws_region"], "ap-northeast-2")
        self.assertEqual(control["terraform_region"], "ap-northeast-2")
        self.assertEqual(control["state_key"], "hoodi-testnet-dev/terraform.tfstate")
        self.assertEqual(
            control["state_kms_key_arn"],
            "arn:aws:kms:ap-northeast-2:123456789012:key/11111111-2222-4333-8444-555555555555",
        )
        self.assertEqual(
            control["plan_artifact_kms_key_arn"],
            "arn:aws:kms:ap-northeast-2:123456789012:key/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        )
        self.assertEqual(
            control["kms_break_glass_role_arn"],
            "arn:aws:iam::123456789012:role/hoodi-testnet-dev-KmsBreakGlassRole",
        )
        self.assertEqual(
            control["canonical_manifest_path"], str(canonical_path.resolve())
        )
        self.assertEqual(control["runtime_manifest_sha256"], manifest_sha)

        machine = json.loads(result.stdout)
        self.assertEqual(machine["status"], "PASS")
        self.assertEqual(machine["mode"], "deploy")
        self.assertEqual(
            machine["artifacts"]["canonical_manifest"]["sha256"], manifest_sha
        )
        for artifact in (
            "canonical_manifest",
            "terraform_tfvars",
            "backend_hcl",
            "control_json",
        ):
            self.assertTrue(Path(machine["artifacts"][artifact]["path"]).is_absolute())
            self.assertRegex(machine["artifacts"][artifact]["sha256"], r"^[0-9a-f]{64}$")
        for sensitive_value in (
            "123456789012",
            "playbuilder47+testnet_op1@gmail.com",
            "TerraformApplyRole",
        ):
            self.assertNotIn(sensitive_value, result.stdout)
        for path in (canonical_path, tfvars_path, backend_path, control_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_modes_change_only_the_protected_destroy_opt_in(self) -> None:
        rendered: dict[str, dict[str, object]] = {}
        for mode, expected in (
            ("deploy", False),
            ("prepare-teardown", True),
            ("teardown", True),
        ):
            self.output = self.root / mode
            result = self.run_helper(mode)
            with self.subTest(mode=mode):
                self.assertEqual(result.returncode, 0, result.stderr)
                tfvars = json.loads(
                    (self.output / "ci.auto.tfvars.json").read_text(encoding="utf-8")
                )
                self.assertIs(tfvars["allow_protected_destroy"], expected)
                control = json.loads(
                    (self.output / "control.json").read_text(encoding="utf-8")
                )
                self.assertEqual(control["mode"], mode)
                rendered[mode] = tfvars
        deploy_without_flag = dict(rendered["deploy"])
        deploy_without_flag.pop("allow_protected_destroy")
        for mode in ("prepare-teardown", "teardown"):
            candidate = dict(rendered[mode])
            candidate.pop("allow_protected_destroy")
            self.assertEqual(candidate, deploy_without_flag)

    def test_rejects_unknown_missing_and_duplicate_schema_members(self) -> None:
        cases: list[tuple[str, object, str]] = []
        unknown_top = copy.deepcopy(VALID_MANIFEST)
        unknown_top["github_token"] = "must-never-have-a-schema-slot"
        cases.append(("unknown_top", unknown_top, "schema_top_level"))
        missing_top = copy.deepcopy(VALID_MANIFEST)
        del missing_top["repository_id"]
        cases.append(("missing_top", missing_top, "schema_top_level"))
        unknown_aws = copy.deepcopy(VALID_MANIFEST)
        unknown_aws["aws"]["access_key_id"] = "must-never-have-a-schema-slot"
        cases.append(("unknown_aws", unknown_aws, "schema_aws"))
        missing_terraform = copy.deepcopy(VALID_MANIFEST)
        del missing_terraform["terraform"]["backup_peer_public_ip"]
        cases.append(("missing_terraform", missing_terraform, "schema_terraform"))

        for name, payload, reason in cases:
            with self.subTest(name=name):
                self.output = self.root / f"out-{name}"
                self.write_manifest(payload)
                self.assert_failed_without_outputs(self.run_helper(), reason)

        self.output = self.root / "out-duplicate"
        text = json.dumps(VALID_MANIFEST)
        self.manifest.write_text(
            text[:-1] + ',"repository_id":"111111111"}', encoding="utf-8"
        )
        self.assert_failed_without_outputs(self.run_helper(), "duplicate_json_key")

    def test_binds_repository_numeric_ids_and_environment_to_expected_context(self) -> None:
        cases = (
            ({"repository": "other/repository"}, {}, "repository_mismatch"),
            ({"repository_owner_id": "1234568"}, {}, "repository_owner_id_mismatch"),
            ({"repository_id": "987654322"}, {}, "repository_id_mismatch"),
            ({"environment": "prod"}, {}, "environment_invalid"),
            ({"repository_owner_id": 1234567}, {}, "repository_owner_id_invalid"),
            ({"repository_id": "01"}, {}, "repository_id_invalid"),
        )
        for index, (updates, cli, reason) in enumerate(cases):
            with self.subTest(updates=updates):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload.update(updates)
                self.write_manifest(payload)
                self.output = self.root / f"identity-{index}"
                self.assert_failed_without_outputs(self.run_helper(**cli), reason)

    def test_rejects_cross_account_or_ambiguous_iam_and_kms_authority(self) -> None:
        mutations = (
            (
                "same_role",
                "apply_role_arn",
                VALID_MANIFEST["aws"]["plan_role_arn"],
                "iam_roles_not_distinct",
            ),
            (
                "wrong_same_account_plan_role",
                "plan_role_arn",
                "arn:aws:iam::123456789012:role/ArbitraryPrivilegedOidcRole",
                "plan_role_arn_invalid",
            ),
            (
                "wrong_same_account_apply_role",
                "apply_role_arn",
                "arn:aws:iam::123456789012:role/AnotherPrivilegedOidcRole",
                "apply_role_arn_invalid",
            ),
            (
                "cross_account_plan",
                "plan_role_arn",
                "arn:aws:iam::999999999999:role/TerraformPlanRole",
                "plan_role_arn_invalid",
            ),
            (
                "cross_region_state_kms",
                "state_kms_key_arn",
                "arn:aws:kms:eu-central-1:123456789012:key/11111111-2222-4333-8444-555555555555",
                "state_kms_key_arn_invalid",
            ),
            (
                "cross_account_artifact_kms",
                "plan_artifact_kms_key_arn",
                "arn:aws:kms:ap-northeast-2:999999999999:key/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "plan_artifact_kms_key_arn_invalid",
            ),
            (
                "cross_account_boundary",
                "node_permissions_boundary_arn",
                "arn:aws:iam::999999999999:policy/node-boundary",
                "node_permissions_boundary_arn_invalid",
            ),
            (
                "wrong_same_account_boundary",
                "node_permissions_boundary_arn",
                "arn:aws:iam::123456789012:policy/ArbitraryBoundary",
                "node_permissions_boundary_arn_invalid",
            ),
            (
                "cross_account_break_glass",
                "kms_break_glass_role_arn",
                "arn:aws:iam::999999999999:role/hoodi-testnet-dev-KmsBreakGlassRole",
                "kms_break_glass_role_arn_invalid",
            ),
            (
                "wrong_break_glass_role",
                "kms_break_glass_role_arn",
                "arn:aws:iam::123456789012:role/other-admin",
                "kms_break_glass_role_arn_invalid",
            ),
            (
                "break_glass_is_apply_role",
                "kms_break_glass_role_arn",
                VALID_MANIFEST["aws"]["apply_role_arn"],
                "iam_roles_not_distinct",
            ),
        )
        for index, (name, key, value, reason) in enumerate(mutations):
            with self.subTest(name=name):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["aws"][key] = value
                self.write_manifest(payload)
                self.output = self.root / f"authority-{index}"
                self.assert_failed_without_outputs(self.run_helper(), reason)

    def test_rejects_invalid_or_overlapping_backend_storage_identity(self) -> None:
        mutations = (
            ("uppercase_bucket", "state_bucket", "Invalid_Bucket", "state_bucket_invalid"),
            (
                "same_bucket",
                "plan_artifact_bucket",
                VALID_MANIFEST["aws"]["state_bucket"],
                "buckets_not_distinct",
            ),
            ("wrong_state_key", "state_key", "other/terraform.tfstate", "state_key_invalid"),
        )
        for index, (name, key, value, reason) in enumerate(mutations):
            with self.subTest(name=name):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["aws"][key] = value
                self.write_manifest(payload)
                self.output = self.root / f"storage-{index}"
                self.assert_failed_without_outputs(self.run_helper(), reason)

    def test_admin_cidrs_are_unique_canonical_global_ipv4_host_routes(self) -> None:
        cases = (
            (["8.8.8.8/32", "8.8.8.8/32"], "admin_cidrs_duplicate"),
            (["8.8.8.0/24"], "admin_cidr_invalid"),
            (["08.8.8.8/32"], "admin_cidr_invalid"),
            (["10.0.0.1/32"], "admin_cidr_not_global"),
            (["2001:4860:4860::8888/128"], "admin_cidr_invalid"),
            ([], "admin_cidrs_invalid"),
        )
        for index, (cidrs, reason) in enumerate(cases):
            with self.subTest(cidrs=cidrs):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["terraform"]["admin_cidrs"] = cidrs
                self.write_manifest(payload)
                self.output = self.root / f"cidr-{index}"
                self.assert_failed_without_outputs(self.run_helper(), reason)

    def test_backup_peer_is_null_or_one_canonical_plain_global_ipv4(self) -> None:
        payload = copy.deepcopy(VALID_MANIFEST)
        payload["terraform"]["backup_peer_public_ip"] = "8.8.4.4"
        self.write_manifest(payload)
        self.assertEqual(self.run_helper().returncode, 0)

        for index, value in enumerate(
            ("8.8.4.4/32", "08.8.4.4", "10.0.0.8", "2001:4860:4860::8844", 8)
        ):
            with self.subTest(value=value):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["terraform"]["backup_peer_public_ip"] = value
                self.write_manifest(payload)
                self.output = self.root / f"peer-{index}"
                self.assert_failed_without_outputs(
                    self.run_helper(), "backup_peer_public_ip_invalid"
                )

    def test_accepts_any_gmail_owner_with_the_two_required_role_aliases(self) -> None:
        payload = copy.deepcopy(VALID_MANIFEST)
        payload["terraform"]["operator_alert_emails"] = [
            "student.team+testnet_op1@gmail.com",
            "student.team+testnet_op2@gmail.com",
        ]
        self.write_manifest(payload)

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(
            (self.output / "ci.auto.tfvars.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            rendered["operator_alert_emails"],
            [
                "student.team+testnet_op1@gmail.com",
                "student.team+testnet_op2@gmail.com",
            ],
        )

    def test_rejects_operator_emails_that_do_not_share_one_gmail_owner(self) -> None:
        cases = (
            [
                "student.one+testnet_op1@gmail.com",
                "student.two+testnet_op2@gmail.com",
            ],
            [
                "student.team+testnet_op2@gmail.com",
                "student.team+testnet_op1@gmail.com",
            ],
            [
                "student.team+testnet_op1@example.com",
                "student.team+testnet_op2@example.com",
            ],
        )
        for index, emails in enumerate(cases):
            with self.subTest(emails=emails):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["terraform"]["operator_alert_emails"] = emails
                self.write_manifest(payload)
                self.output = self.root / f"operator-emails-{index}"
                self.assert_failed_without_outputs(
                    self.run_helper(), "operator_alert_emails_invalid"
                )

    def test_rejects_changes_to_fixed_terraform_course_contract(self) -> None:
        cases = (
            ("network", "mainnet", "network_invalid"),
            ("region", "not-a-region", "terraform_region_invalid"),
            ("kms_recovery_region", "ap-northeast-2", "kms_recovery_region_invalid"),
            ("key_pair_name", "other-key", "key_pair_name_invalid"),
            (
                "sso_operator_permission_sets",
                ["testnet_operator_02_approver", "testnet_operator_01_builder"],
                "sso_operator_permission_sets_invalid",
            ),
            (
                "operator_alert_emails",
                ["operator1@example.com", "operator2@example.com"],
                "operator_alert_emails_invalid",
            ),
            ("enable_deadman_alarm", "false", "enable_deadman_alarm_invalid"),
            ("enable_staging_bucket", 1, "enable_staging_bucket_invalid"),
        )
        for index, (key, value, reason) in enumerate(cases):
            with self.subTest(key=key):
                payload = copy.deepcopy(VALID_MANIFEST)
                payload["terraform"][key] = value
                self.write_manifest(payload)
                self.output = self.root / f"terraform-{index}"
                self.assert_failed_without_outputs(self.run_helper(), reason)

    def test_rejects_symlink_manifest_and_symlink_output_artifact(self) -> None:
        real_manifest = self.root / "real-runtime-inputs.json"
        real_manifest.write_bytes(self.manifest.read_bytes())
        self.manifest.unlink()
        self.manifest.symlink_to(real_manifest)
        self.assert_failed_without_outputs(self.run_helper(), "manifest_symlink")

        self.manifest.unlink()
        self.manifest = real_manifest
        self.output.mkdir()
        protected = self.root / "protected"
        protected.write_text("do not replace\n", encoding="utf-8")
        (self.output / "control.json").symlink_to(protected)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=output_symlink", result.stderr)
        self.assertEqual(protected.read_text(encoding="utf-8"), "do not replace\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
