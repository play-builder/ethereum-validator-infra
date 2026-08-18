#!/usr/bin/env python3
"""Contracts for local SSH key ownership and bootstrap public-key import."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "primary-aws/bootstrap/cicd/template.yaml"
DEPLOY = ROOT / ".github/workflows/terraform-deploy.yml"
TEARDOWN = ROOT / ".github/workflows/terraform-teardown.yml"
EC2_TF = ROOT / "primary-aws/terraform/ec2.tf"


class TerraformCicdKeyHandoffTests(unittest.TestCase):
    def test_bootstrap_imports_only_the_local_ed25519_public_key(self) -> None:
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        name = template["Parameters"]["NodeSshKeyPairName"]
        public_key = template["Parameters"]["NodeSshPublicKey"]
        resource = template["Resources"]["NodeSshKeyPair"]

        self.assertEqual(name["Default"], "eth-failover-hoodi")
        self.assertEqual(name["AllowedValues"], ["eth-failover-hoodi"])
        self.assertRegex(public_key["AllowedPattern"], r"ssh-ed25519")
        self.assertEqual(resource["Type"], "AWS::EC2::KeyPair")
        self.assertEqual(
            resource["Properties"]["KeyName"], {"Ref": "NodeSshKeyPairName"}
        )
        self.assertEqual(
            resource["Properties"]["PublicKeyMaterial"],
            {"Ref": "NodeSshPublicKey"},
        )
        self.assertEqual(
            {
                ("Project", "eth-failover"),
                ("Network", "hoodi"),
                ("Managed", "cloudformation"),
            },
            {
                (tag["Key"], tag["Value"])
                for tag in resource["Properties"]["Tags"]
            },
        )

    def test_bootstrap_exports_exact_key_name_and_fingerprint(self) -> None:
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        outputs = template["Outputs"]
        self.assertEqual(outputs["NodeSshKeyPairName"]["Value"], {"Ref": "NodeSshKeyPair"})
        self.assertEqual(
            outputs["NodeSshKeyFingerprint"]["Value"],
            {"Fn::GetAtt": ["NodeSshKeyPair", "KeyFingerprint"]},
        )

    def test_workload_terraform_and_workflows_receive_no_public_key(self) -> None:
        joined = "\n".join(
            path.read_text(encoding="utf-8") for path in (DEPLOY, TEARDOWN, EC2_TF)
        )
        self.assertNotIn("TF_VAR_SSH_PUBLIC_KEY", joined)
        self.assertNotIn("CI_SSH_PUBLIC_KEY", joined)
        self.assertNotIn("ssh_public_key", joined)
        self.assertNotIn('resource "aws_key_pair"', joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
