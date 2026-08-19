#!/usr/bin/env python3
"""Behavior tests for the Identity Center readback verifier."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "shared/scripts/verify-identity-center.sh"


FAKE_AWS = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
service, action = args[0], args[1]
query = args[args.index("--query") + 1] if "--query" in args else ""
op1_user = os.environ["FAKE_OP1_USER"]
op2_user = os.environ["FAKE_OP2_USER"]

if (service, action) == ("sso-admin", "list-instances"):
    print("arn:aws:sso:::instance/ssoins-example d-example")
elif (service, action) == ("sso-admin", "list-permission-sets"):
    print("arn:aws:sso:::permissionSet/ssoins-example/ps-op1\tarn:aws:sso:::permissionSet/ssoins-example/ps-op2")
elif (service, action) == ("sso-admin", "describe-permission-set"):
    arn = args[args.index("--permission-set-arn") + 1]
    if query == "PermissionSet.Name":
        print("testnet_operator_01_builder" if arn.endswith("ps-op1") else "testnet_operator_02_approver")
    else:
        print("PT4H")
elif (service, action) == ("sso-admin", "get-inline-policy-for-permission-set"):
    arn = args[args.index("--permission-set-arn") + 1]
    relative = "primary-aws/bootstrap/operator-1-builder.json" if arn.endswith("ps-op1") else "primary-aws/bootstrap/operator-2-approver.json"
    print(Path(os.environ["FAKE_REPO_ROOT"], relative).read_text(encoding="utf-8"))
elif (service, action) == ("sso-admin", "list-managed-policies-in-permission-set"):
    print("0")
elif (service, action) == ("identitystore", "list-users"):
    if op1_user in query:
        print(os.environ["FAKE_OP1_EMAIL"] if "Emails" in query else "uid-op1")
    elif op2_user in query:
        print(os.environ["FAKE_OP2_EMAIL"] if "Emails" in query else "uid-op2")
elif (service, action) == ("sso-admin", "list-account-assignments"):
    arn = args[args.index("--permission-set-arn") + 1]
    uid = "uid-op1" if arn.endswith("ps-op1") else "uid-op2"
    print(f"{uid}\tUSER\t123456789012")
elif (service, action) == ("sso-admin", "list-permission-sets-provisioned-to-account"):
    print("arn:aws:sso:::permissionSet/ssoins-example/ps-op1\tarn:aws:sso:::permissionSet/ssoins-example/ps-op2")
else:
    print(json.dumps({"unexpected": args}), file=sys.stderr)
    raise SystemExit(2)
'''


class IdentityCenterVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bin_dir = Path(self.temporary.name)
        fake = self.bin_dir / "aws"
        fake.write_text(textwrap.dedent(FAKE_AWS), encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "PATH": f"{self.bin_dir}:{self.environment['PATH']}",
                "FAKE_REPO_ROOT": str(ROOT),
                "FAKE_OP1_USER": "student_operator_01",
                "FAKE_OP2_USER": "student_operator_02",
                "FAKE_OP1_EMAIL": "student.team+testnet_op1@gmail.com",
                "FAKE_OP2_EMAIL": "student.team+testnet_op2@gmail.com",
                "OP1_SSO_USER": "student_operator_01",
                "OP2_SSO_USER": "student_operator_02",
                "OP1_EMAIL": "student.team+testnet_op1@gmail.com",
                "OP2_EMAIL": "student.team+testnet_op2@gmail.com",
            }
        )

    def run_verifier(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(VERIFIER),
                "--profile",
                "student-team-identity-admin",
                "--account-id",
                "123456789012",
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_uses_environment_usernames_and_accepts_matching_custom_gmail(self) -> None:
        result = self.run_verifier()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IDENTITY_READBACK=PASS", result.stdout)
        self.assertIn("student.team+testnet_op1@gmail.com", result.stdout)
        self.assertIn("student.team+testnet_op2@gmail.com", result.stdout)

    def test_rejects_identity_center_email_that_differs_from_lab_env(self) -> None:
        self.environment["FAKE_OP1_EMAIL"] = "other.team+testnet_op1@gmail.com"

        result = self.run_verifier()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHECK op1_email", result.stdout)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("IDENTITY_READBACK=FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
