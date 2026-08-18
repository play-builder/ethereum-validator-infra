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
HELPER = ROOT / "primary-aws/bootstrap/cicd/render-parameters.py"
TEMPLATE = ROOT / "primary-aws/bootstrap/cicd/template.yaml"
PARAMETERS_SOURCE_PATH = ROOT / "primary-aws/bootstrap/cicd/parameters.json"

REPOSITORY = "play-builder/ethereum-validator-infra"
OWNER_ID = "1234567"
REPOSITORY_ID = "987654321"
ACCOUNT_ID = "123456789012"
REGION = "ap-northeast-2"
STATE_BUCKET = f"hoodi-testnet-dev-tfstate-{ACCOUNT_ID}"
PLAN_BUCKET = f"hoodi-testnet-dev-tfplans-{ACCOUNT_ID}"
PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f "
    "hoodi-testnet-dev/operator-1"
)

VALID_RUNTIME_MANIFEST = {
    "schema_version": 1,
    "repository": REPOSITORY,
    "repository_owner_id": OWNER_ID,
    "repository_id": REPOSITORY_ID,
    "environment": "hoodi-testnet-dev",
    "aws": {
        "account_id": ACCOUNT_ID,
        "region": REGION,
        "state_key": "hoodi-testnet-dev/terraform.tfstate",
        "plan_role_arn": (
            f"arn:aws:iam::{ACCOUNT_ID}:role/hoodi-testnet-dev-TerraformPlanRole"
        ),
        "apply_role_arn": (
            f"arn:aws:iam::{ACCOUNT_ID}:role/hoodi-testnet-dev-TerraformApplyRole"
        ),
        "kms_break_glass_role_arn": (
            f"arn:aws:iam::{ACCOUNT_ID}:role/hoodi-testnet-dev-KmsBreakGlassRole"
        ),
        "state_bucket": STATE_BUCKET,
        "state_kms_key_arn": (
            f"arn:aws:kms:{REGION}:{ACCOUNT_ID}:key/"
            "11111111-2222-3333-4444-555555555555"
        ),
        "plan_artifact_bucket": PLAN_BUCKET,
        "plan_artifact_kms_key_arn": (
            f"arn:aws:kms:{REGION}:{ACCOUNT_ID}:key/"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        ),
        "node_permissions_boundary_arn": (
            f"arn:aws:iam::{ACCOUNT_ID}:policy/"
            "hoodi-testnet-dev-node-permissions-boundary"
        ),
    },
    "terraform": {
        "network": "hoodi",
        "kms_recovery_region": "eu-central-1",
        "key_pair_name": "eth-failover-hoodi",
        "admin_cidrs": ["8.8.8.8/32"],
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


class BootstrapParameterEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.parameters = self.directory / "parameters.json"
        self.public_key = self.directory / "node.pub"
        self.public_key.write_text(PUBLIC_KEY + "\n", encoding="utf-8")
        self.manifest = self.directory / "runtime-inputs.json"
        self.write_manifest(VALID_RUNTIME_MANIFEST)

    def write_manifest(self, payload: object) -> None:
        self.manifest.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def command(self, mode: str) -> list[str]:
        command = [
            "python3",
            str(HELPER),
            "--mode",
            mode,
            "--template",
            str(TEMPLATE),
            "--parameters",
            str(self.parameters),
            "--expected-repository",
            REPOSITORY,
            "--expected-owner-id",
            OWNER_ID,
            "--expected-repository-id",
            REPOSITORY_ID,
            "--expected-account-id",
            ACCOUNT_ID,
            "--expected-region",
            REGION,
        ]
        if mode == "render":
            command.extend(
                [
                    "--state-bucket-mode",
                    "CREATE",
                    "--state-bucket-name",
                    STATE_BUCKET,
                    "--plan-artifact-bucket-name",
                    PLAN_BUCKET,
                    "--node-ssh-public-key-file",
                    str(self.public_key),
                ]
            )
        else:
            command.extend(["--runtime-manifest", str(self.manifest)])
        return command

    def run_helper(self, mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(mode), text=True, capture_output=True, check=False
        )

    def render_valid_parameters(self) -> list[dict[str, str]]:
        result = self.run_helper("render")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.parameters.read_text(encoding="utf-8"))

    def assert_failed(self, result: subprocess.CompletedProcess[str], reason: str) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"reason={reason}", result.stderr)

    def test_render_uses_explicit_trusted_context_without_requiring_runtime_manifest(self) -> None:
        self.manifest.unlink()

        result = self.run_helper("render")

        self.assertEqual(result.returncode, 0, result.stderr)
        canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        parameters = json.loads(self.parameters.read_text(encoding="utf-8"))
        values = {item["ParameterKey"]: item["ParameterValue"] for item in parameters}
        self.assertEqual(len(parameters), 20)
        self.assertEqual(set(values), set(canonical["Parameters"]))
        self.assertEqual(
            values,
            {
                "GitHubRepository": REPOSITORY,
                "GitHubRepositoryOwnerId": OWNER_ID,
                "GitHubRepositoryId": REPOSITORY_ID,
                "GitHubEnvironment": "hoodi-testnet-dev",
                "GitHubTeardownEnvironment": "hoodi-testnet-dev-teardown",
                "ExistingGithubOidcProviderArn": "",
                "TerraformPlanRoleName": "hoodi-testnet-dev-TerraformPlanRole",
                "TerraformApplyRoleName": "hoodi-testnet-dev-TerraformApplyRole",
                "NodeRoleName": "eth-failover-hoodi-node",
                "NodeInstanceProfileName": "eth-failover-hoodi-node",
                "NodeSshKeyPairName": "eth-failover-hoodi",
                "NodeSshPublicKey": PUBLIC_KEY,
                "NodePermissionsBoundaryName": (
                    "hoodi-testnet-dev-node-permissions-boundary"
                ),
                "StateBucketMode": "CREATE",
                "StateBucketName": STATE_BUCKET,
                "ExistingStateBucketName": "",
                "ExistingStateKmsKeyArn": "",
                "StateKey": "hoodi-testnet-dev/terraform.tfstate",
                "PlanArtifactBucketName": PLAN_BUCKET,
                "CloudTrailTrailArn": "",
            },
        )
        expected_sha = hashlib.sha256(self.parameters.read_bytes()).hexdigest()
        machine = json.loads(result.stdout)
        self.assertEqual(machine["status"], "PASS")
        self.assertEqual(machine["mode"], "render")
        self.assertEqual(machine["parameter_count"], 20)
        self.assertEqual(machine["sha256"], expected_sha)
        self.assertEqual(stat.S_IMODE(self.parameters.stat().st_mode), 0o644)

    def test_check_is_order_independent_and_cross_binds_the_actual_runtime_manifest(self) -> None:
        parameters = self.render_valid_parameters()
        self.parameters.write_text(
            json.dumps(list(reversed(parameters)), indent=2) + "\n",
            encoding="utf-8",
        )

        result = self.run_helper("check")

        self.assertEqual(result.returncode, 0, result.stderr)
        machine = json.loads(result.stdout)
        self.assertEqual(machine["status"], "PASS")
        self.assertEqual(machine["mode"], "check")
        self.assertEqual(
            machine["sha256"],
            hashlib.sha256(self.parameters.read_bytes()).hexdigest(),
        )

    def test_check_rejects_unknown_missing_and_duplicate_parameters(self) -> None:
        baseline = self.render_valid_parameters()
        cases = []
        unknown = copy.deepcopy(baseline)
        unknown.append({"ParameterKey": "AccessKeyId", "ParameterValue": "forbidden"})
        cases.append(("unknown", unknown, "parameter_key_set"))
        missing = copy.deepcopy(baseline[:-1])
        cases.append(("missing", missing, "parameter_key_set"))
        duplicate = copy.deepcopy(baseline)
        duplicate.append(copy.deepcopy(baseline[0]))
        cases.append(("duplicate", duplicate, "duplicate_parameter"))

        for name, payload, reason in cases:
            with self.subTest(name=name):
                self.parameters.write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
                self.assert_failed(self.run_helper("check"), reason)

    def test_check_rejects_runtime_manifest_cross_binding_drift(self) -> None:
        self.render_valid_parameters()
        cases = (
            (("repository",), "other/repository", "repository_mismatch"),
            (("repository_owner_id",), "7654321", "owner_id_mismatch"),
            (("repository_id",), "123456789", "repository_id_mismatch"),
            (("aws", "account_id"), "123456789013", "account_id_mismatch"),
            (("aws", "region"), "eu-west-1", "region_mismatch"),
            (("environment",), "production", "environment_mismatch"),
            (("aws", "state_key"), "other/terraform.tfstate", "state_key_mismatch"),
            (("aws", "state_bucket"), "other-state-123456789012", "state_bucket_mismatch"),
            (("aws", "plan_artifact_bucket"), "other-plan-123456789012", "plan_bucket_mismatch"),
            (("terraform", "key_pair_name"), "other-key", "key_pair_mismatch"),
            (
                ("aws", "plan_role_arn"),
                f"arn:aws:iam::{ACCOUNT_ID}:role/other-plan-role",
                "plan_role_mismatch",
            ),
            (
                ("aws", "apply_role_arn"),
                f"arn:aws:iam::{ACCOUNT_ID}:role/other-apply-role",
                "apply_role_mismatch",
            ),
            (
                ("aws", "node_permissions_boundary_arn"),
                f"arn:aws:iam::{ACCOUNT_ID}:policy/other-boundary",
                "node_boundary_mismatch",
            ),
        )
        for index, (path, value, reason) in enumerate(cases):
            with self.subTest(path=path):
                payload = copy.deepcopy(VALID_RUNTIME_MANIFEST)
                target = payload
                for segment in path[:-1]:
                    target = target[segment]
                target[path[-1]] = value
                self.write_manifest(payload)
                self.assert_failed(self.run_helper("check"), reason)
                self.write_manifest(VALID_RUNTIME_MANIFEST)

        parameters = self.render_valid_parameters()
        for item in parameters:
            if item["ParameterKey"] == "GitHubTeardownEnvironment":
                item["ParameterValue"] = "other-teardown"
        self.parameters.write_text(json.dumps(parameters) + "\n", encoding="utf-8")
        self.assert_failed(
            self.run_helper("check"), "teardown_environment_mismatch"
        )

    def test_existing_mode_requires_and_cross_binds_the_external_state_store(self) -> None:
        existing_bucket = f"existing-hoodi-state-{ACCOUNT_ID}"
        existing_key = (
            f"arn:aws:kms:{REGION}:{ACCOUNT_ID}:key/"
            "99999999-8888-7777-6666-555555555555"
        )
        command = self.command("render")
        mode_index = command.index("CREATE")
        command[mode_index] = "EXISTING"
        state_bucket_index = command.index(STATE_BUCKET)
        command[state_bucket_index] = existing_bucket
        command.extend(["--existing-state-kms-key-arn", existing_key])

        rendered = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        parameters = json.loads(self.parameters.read_text(encoding="utf-8"))
        values = {item["ParameterKey"]: item["ParameterValue"] for item in parameters}
        self.assertEqual(values["StateBucketMode"], "EXISTING")
        self.assertEqual(values["StateBucketName"], "")
        self.assertEqual(values["ExistingStateBucketName"], existing_bucket)
        self.assertEqual(values["ExistingStateKmsKeyArn"], existing_key)

        manifest = copy.deepcopy(VALID_RUNTIME_MANIFEST)
        manifest["aws"]["state_bucket"] = existing_bucket
        manifest["aws"]["state_kms_key_arn"] = existing_key
        self.write_manifest(manifest)
        checked = self.run_helper("check")
        self.assertEqual(checked.returncode, 0, checked.stderr)

        missing_key_command = [
            value
            for index, value in enumerate(command)
            if not (
                value == "--existing-state-kms-key-arn"
                or (index > 0 and command[index - 1] == "--existing-state-kms-key-arn")
            )
        ]
        self.parameters.unlink()
        missing = subprocess.run(
            missing_key_command, text=True, capture_output=True, check=False
        )
        self.assert_failed(missing, "existing_state_kms_key_arn")

        wrong_region_command = command.copy()
        wrong_region_command[wrong_region_command.index(existing_key)] = (
            f"arn:aws:kms:eu-west-1:{ACCOUNT_ID}:key/"
            "99999999-8888-7777-6666-555555555555"
        )
        wrong_region = subprocess.run(
            wrong_region_command, text=True, capture_output=True, check=False
        )
        self.assert_failed(wrong_region, "existing_state_kms_key_arn")

    def test_oidc_provider_is_empty_or_the_exact_same_account_github_provider(self) -> None:
        accepted = self.command("render") + [
            "--existing-github-oidc-provider-arn",
            f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com",
        ]
        result = subprocess.run(accepted, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

        rejected = accepted.copy()
        rejected[-1] = (
            "arn:aws:iam::123456789013:oidc-provider/"
            "token.actions.githubusercontent.com"
        )
        result = subprocess.run(rejected, text=True, capture_output=True, check=False)
        self.assert_failed(result, "oidc_provider_arn")

    def test_parameter_output_and_arbitrary_values_fail_closed(self) -> None:
        target = self.directory / "must-not-change.json"
        target.write_text("sentinel\n", encoding="utf-8")
        os.symlink(target.name, self.parameters)
        result = self.run_helper("render")
        self.assert_failed(result, "output_not_regular")
        self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")

        self.parameters.unlink()
        parameters = self.render_valid_parameters()
        for item in parameters:
            if item["ParameterKey"] == "TerraformPlanRoleName":
                item["ParameterValue"] = "REPLACE_WITH_PLAN_ROLE"
        self.parameters.write_text(json.dumps(parameters) + "\n", encoding="utf-8")
        self.assert_failed(self.run_helper("check"), "placeholder_value")

        overwrite_command = self.command("render")
        overwrite_command[overwrite_command.index(str(self.parameters))] = str(TEMPLATE)
        result = subprocess.run(
            overwrite_command, text=True, capture_output=True, check=False
        )
        self.assert_failed(result, "output_overwrites_input")

    def test_public_key_and_input_files_fail_closed(self) -> None:
        invalid_public_keys = (
            ("placeholder", "REPLACE_WITH_PUBLIC_KEY\n", "public_key_format"),
            (
                "private",
                "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n",
                "private_key_forbidden",
            ),
            ("multiline", PUBLIC_KEY + "\nsecond-line\n", "public_key_single_line"),
            (
                "invalid_blob",
                "ssh-ed25519 " + "A" * 68 + " invalid\n",
                "public_key_blob",
            ),
        )
        for name, content, reason in invalid_public_keys:
            with self.subTest(name=name):
                self.public_key.write_text(content, encoding="utf-8")
                self.assert_failed(self.run_helper("render"), reason)
                self.assertFalse(self.parameters.exists())

        self.public_key.unlink()
        target = self.directory / "actual.pub"
        target.write_text(PUBLIC_KEY + "\n", encoding="utf-8")
        os.symlink(target.name, self.public_key)
        self.assert_failed(self.run_helper("render"), "public_key_not_regular")
        self.assertFalse(self.parameters.exists())

        self.public_key.unlink()
        self.public_key.write_text(PUBLIC_KEY + "\n", encoding="utf-8")
        self.render_valid_parameters()
        parameters_target = self.directory / "actual-parameters.json"
        self.parameters.replace(parameters_target)
        os.symlink(parameters_target.name, self.parameters)
        self.assert_failed(self.run_helper("check"), "parameters_not_regular")


class BootstrapTemplateTransportTests(unittest.TestCase):
    def test_pretty_template_compacts_below_cloudformation_inline_limit(self) -> None:
        raw = TEMPLATE.read_bytes()
        compact = json.dumps(
            json.loads(raw.decode("utf-8")), separators=(",", ":")
        ).encode("utf-8")
        self.assertGreater(len(raw), 51_200)
        self.assertLessEqual(len(compact), 51_200)
        readme = (ROOT / "primary-aws/bootstrap/cicd/README.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            "--template-body file://primary-aws/bootstrap/cicd/template.yaml",
            readme,
        )
        self.assertIn('COMPACT_TEMPLATE="/tmp/cicd-bootstrap.compact.json"', readme)


if __name__ == "__main__":
    unittest.main()
