#!/usr/bin/env python3
"""Tests for read-only AWS API discovery and atomic lab.env publication."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "shared/scripts/sync-aws-runtime-outputs.py"


FAKE_AWS = r'''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
mode = os.environ.get("FAKE_AWS_MODE", "ok")
with open(os.environ["FAKE_AWS_LOG"], "a", encoding="utf-8") as stream:
    stream.write(" ".join(args) + "\n")

service, operation = args[0:2]
account = "999999999999" if mode == "wrong_account" else "123456789012"

if (service, operation) == ("sts", "get-caller-identity"):
    payload = {
        "Account": account,
        "Arn": f"arn:aws:sts::{account}:assumed-role/AWSReservedSSO_testnet_operator_01_audit/example",
        "UserId": "AROAEXAMPLE:example",
    }
elif (service, operation) == ("ec2", "describe-instances"):
    instance = {
        "InstanceId": "i-0123456789abcdef0",
        "State": {"Name": "running"},
        "Placement": {"AvailabilityZone": "ap-northeast-2a"},
        "Tags": [
            {"Key": "Project", "Value": "eth-failover"},
            {"Key": "Network", "Value": "hoodi"},
            {"Key": "Role", "Value": "primary"},
            {"Key": "Managed", "Value": "terraform"},
        ],
    }
    instances = [instance, instance] if mode == "duplicate_instance" else [instance]
    payload = {"Reservations": [{"Instances": instances}]}
elif (service, operation) == ("ec2", "describe-addresses"):
    payload = {"Addresses": [{
        "PublicIp": "203.0.113.30" if mode == "nonglobal_ip" else "8.8.8.8",
        "Domain": "vpc",
        "InstanceId": "i-0123456789abcdef0",
        "Tags": [
            {"Key": "Project", "Value": "eth-failover"},
            {"Key": "Network", "Value": "hoodi"},
            {"Key": "Role", "Value": "primary"},
            {"Key": "Managed", "Value": "terraform"},
        ],
    }]}
elif (service, operation) == ("sns", "list-topics"):
    topic = "wrong-topic" if mode == "wrong_sns" else "eth-failover-hoodi-alerts"
    payload = {"Topics": [{"TopicArn": f"arn:aws:sns:ap-northeast-2:{account}:{topic}"}]}
elif (service, operation) == ("sns", "list-tags-for-resource"):
    payload = {"Tags": [
        {"Key": "Project", "Value": "eth-failover"},
        {"Key": "Network", "Value": "hoodi"},
        {"Key": "Role", "Value": "primary"},
        {"Key": "Managed", "Value": "terraform"},
    ]}
elif (service, operation) == ("ssm", "describe-parameters"):
    prefix = "/wrong/hoodi" if mode == "wrong_ssm" else "/eth-staking/hoodi"
    payload = {"Parameters": [
        {"Name": f"{prefix}/checkpoint_sync_url", "ARN": f"arn:aws:ssm:ap-northeast-2:{account}:parameter{prefix}/checkpoint_sync_url"},
        {"Name": f"{prefix}/graffiti", "ARN": f"arn:aws:ssm:ap-northeast-2:{account}:parameter{prefix}/graffiti"},
        {"Name": f"{prefix}/fee_recipient", "ARN": f"arn:aws:ssm:ap-northeast-2:{account}:parameter{prefix}/fee_recipient"},
    ]}
else:
    print(f"unexpected fake aws command: {service} {operation}", file=sys.stderr)
    raise SystemExit(90)

json.dump(payload, sys.stdout)
'''


class SyncAwsRuntimeOutputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fake_aws = self.root / "aws"
        self.fake_aws.write_text(FAKE_AWS, encoding="utf-8")
        self.fake_aws.chmod(0o755)
        self.log = self.root / "aws.log"
        self.lab_env = self.root / "lab.env"
        self.lab_env.write_text(
            textwrap.dedent(
                """\
                export KEEP=value
                export NODE_IP=198.51.100.10
                export NODE_IP=198.51.100.11
                export INSTANCE_ID=i-00000000000000000
                """
            ),
            encoding="utf-8",
        )
        self.lab_env.chmod(0o640)

    def run_helper(self, mode: str = "ok") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(FAKE_AWS_MODE=mode, FAKE_AWS_LOG=str(self.log))
        return subprocess.run(
            [
                "python3",
                str(HELPER),
                "--aws-bin",
                str(self.fake_aws),
                "--profile",
                "hoodi-testnet-dev-audit",
                "--region",
                "ap-northeast-2",
                "--expected-account-id",
                "123456789012",
                "--network",
                "hoodi",
                "--lab-env",
                str(self.lab_env),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_discovers_exact_tagged_resources_and_atomically_upserts_four_values(self) -> None:
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "LAB_ENV_UPSERT=OK name=NODE_IP",
                "LAB_ENV_UPSERT=OK name=INSTANCE_ID",
                "LAB_ENV_UPSERT=OK name=TOPIC_ARN",
                "LAB_ENV_UPSERT=OK name=PARAM_PREFIX",
                "AWS_RUNTIME_SYNC=PASS count=4",
            ],
        )
        text = self.lab_env.read_text(encoding="utf-8")
        self.assertIn("export KEEP=value", text)
        self.assertEqual(text.count("export NODE_IP="), 1)
        self.assertIn("export NODE_IP=8.8.8.8", text)
        self.assertIn("export INSTANCE_ID=i-0123456789abcdef0", text)
        self.assertIn(
            "export TOPIC_ARN=arn:aws:sns:ap-northeast-2:123456789012:eth-failover-hoodi-alerts",
            text,
        )
        self.assertIn("export PARAM_PREFIX=/eth-staking/hoodi", text)
        self.assertEqual(stat.S_IMODE(self.lab_env.stat().st_mode), 0o640)
        calls = self.log.read_text(encoding="utf-8")
        for marker in (
            "sts get-caller-identity",
            "ec2 describe-instances",
            "ec2 describe-addresses",
            "sns list-topics",
            "sns list-tags-for-resource",
            "ssm describe-parameters",
            "tag:Project",
            "tag:Network",
            "tag:Role",
            "tag:Managed",
        ):
            self.assertIn(marker, calls)
        self.assertFalse(
            any(line.startswith("terraform ") for line in calls.splitlines()),
            "the helper must use read-only AWS API calls, not Terraform",
        )

    def test_wrong_account_fails_without_changing_lab_env(self) -> None:
        before = self.lab_env.read_bytes()
        result = self.run_helper("wrong_account")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=account_mismatch", result.stderr)
        self.assertEqual(before, self.lab_env.read_bytes())

    def test_duplicate_instance_fails_without_changing_lab_env(self) -> None:
        before = self.lab_env.read_bytes()
        result = self.run_helper("duplicate_instance")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=instance_cardinality", result.stderr)
        self.assertEqual(before, self.lab_env.read_bytes())

    def test_non_global_ipv4_fails_without_changing_lab_env(self) -> None:
        before = self.lab_env.read_bytes()
        result = self.run_helper("nonglobal_ip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=node_ip_not_global_ipv4", result.stderr)
        self.assertEqual(before, self.lab_env.read_bytes())

    def test_wrong_sns_or_ssm_identity_fails_closed(self) -> None:
        for mode, reason in (
            ("wrong_sns", "sns_topic_cardinality"),
            ("wrong_ssm", "ssm_parameter_set_mismatch"),
        ):
            before = self.lab_env.read_bytes()
            result = self.run_helper(mode)
            with self.subTest(mode=mode):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"reason={reason}", result.stderr)
                self.assertEqual(before, self.lab_env.read_bytes())

    def test_symlink_lab_env_is_rejected(self) -> None:
        target = self.root / "real.env"
        target.write_text("export KEEP=value\n", encoding="utf-8")
        self.lab_env.unlink()
        self.lab_env.symlink_to(target)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=lab_env_symlink", result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "export KEEP=value\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
