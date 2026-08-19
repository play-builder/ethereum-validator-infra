from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAB_ENV = ROOT / "lab.env.example"
TFVARS_VALIDATOR = ROOT / "shared/scripts/validate-sso-permission-sets.py"


def source_lab_env(slug: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "aws-config"
        config.write_text("", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "AWS_CONFIG_FILE": str(config),
                "AWS_EC2_METADATA_DISABLED": "true",
                "GMAIL_BASE_EMAIL": "student.team@gmail.com",
                "LAB_ORG_SLUG": slug,
            }
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1" && printf "%s\\n" "$IDENTITY_ADMIN_PERMISSION_SET"',
                "_",
                str(LAB_ENV),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )


class PermissionSetNameLimitTests(unittest.TestCase):
    def test_all_course_permission_set_names_fit_aws_limit(self) -> None:
        names = (
            "playbuilder-identity-admin",
            "testnet_operator_01_builder",
            "testnet_operator_02_approver",
            "terraform_cicd_bootstrap_admin",
        )
        for name in names:
            self.assertLessEqual(len(name), 32, name)

    def test_lab_slug_derives_a_permission_set_with_at_most_32_characters(self) -> None:
        result = source_lab_env("abcdefghijklmnopq")
        self.assertEqual(result.returncode, 0, result.stderr)
        name = result.stdout.strip()
        self.assertEqual(name, "abcdefghijklmnopq-identity-admin")
        self.assertEqual(len(name), 32)

    def test_lab_slug_longer_than_17_characters_is_rejected(self) -> None:
        result = source_lab_env("abcdefghijklmnopqr")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LAB_ORG_SLUG는 17자 이하여야", result.stderr)

    def test_removed_overlength_suffix_is_absent_from_student_repository(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
        )
        self.assertNotIn("identity-center" + "-admin", text)

    def test_primary_and_standby_terraform_reject_overlength_names(self) -> None:
        for relative in (
            "primary-aws/terraform/variables-kms.tf",
            "standby-aws/terraform/variables.tf",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('^[A-Za-z0-9_+=,.@-]{1,32}$', text, relative)

    def test_tfvars_validator_rejects_an_overlength_expected_name(self) -> None:
        overlength = "a" * 33
        with tempfile.TemporaryDirectory() as directory:
            tfvars = Path(directory) / "terraform.tfvars"
            tfvars.write_text(
                'sso_operator_permission_sets = ["' + overlength + '", "valid-name"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(TFVARS_VALIDATOR),
                    "--file",
                    str(tfvars),
                    "--expected",
                    overlength,
                    "--expected",
                    "valid-name",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_expected_contract", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
