from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "shared/config/failover-incident-policy.json"
SCHEMA = ROOT / "shared/schemas/incident-classification-v1.json"
HELPER = ROOT / "shared/scripts/classify-failover-incident.py"


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


class IncidentClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in (POLICY, SCHEMA, HELPER):
            self.assertTrue(path.is_file(), f"required Task 2 path missing: {path}")
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory(prefix="incident-classification-")
        self.work = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def classify(
        self, payload: dict | str, *, policy: Path = POLICY
    ) -> subprocess.CompletedProcess[str]:
        input_path = self.work / "incident.json"
        input_path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )
        return subprocess.run(
            [
                "python3",
                str(HELPER),
                "--policy",
                str(policy),
                "--input",
                str(input_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def assert_classification(
        self, payload: dict | str, expected: str, *, policy: Path = POLICY
    ) -> None:
        result = self.classify(payload, policy=policy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected + "\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def base(self, incident_type: str) -> dict:
        return {
            "schema_version": "incident-classification-v1",
            "incident_id": "INC-2026-0001",
            "incident_type": incident_type,
            "primary_execution_client": "nethermind",
            "standby_execution_client": "besu",
            "affected_sites": ["primary-aws"],
            "hard_fence_evidence_hash": digest("hard-fence"),
            "change_id": None,
            "approval_evidence_hash": None,
            "approvers": [],
        }

    def test_same_nethermind_common_mode_is_no_go(self):
        payload = self.base("COMMON_CLIENT_FAILURE")
        payload.update(
            primary_execution_client="nethermind",
            standby_execution_client="nethermind",
            affected_sites=["primary-aws", "standby-standby"],
        )
        self.assert_classification(payload, "NO_GO_COMMON_CLIENT")
        spoofed_site_failure = {
            **self.base("SITE_FAILURE"),
            "primary_execution_client": "nethermind",
            "standby_execution_client": "nethermind",
            "affected_sites": ["primary-aws"],
        }
        self.assert_classification(spoofed_site_failure, "NO_GO_COMMON_CLIENT")
        self.assertEqual(
            self.policy["outputs"],
            [
                "ALLOW_PLANNED_DRILL",
                "ALLOW_SITE_FAILURE_REVIEW",
                "NO_GO_COMMON_CLIENT",
                "NO_GO_UNCLASSIFIED",
            ],
        )

    def test_unknown_incident_is_no_go(self):
        self.assert_classification(self.base("UNKNOWN"), "NO_GO_UNCLASSIFIED")
        self.assert_classification("{not-json", "NO_GO_UNCLASSIFIED")
        duplicate = '{"schema_version":"incident-classification-v1","incident_type":"SITE_FAILURE","incident_type":"PLANNED_DRILL"}'
        self.assert_classification(duplicate, "NO_GO_UNCLASSIFIED")
        with_unknown = {**self.base("SITE_FAILURE"), "unexpected": True}
        self.assert_classification(with_unknown, "NO_GO_UNCLASSIFIED")

    def test_planned_drill_requires_exact_approval_evidence(self):
        valid = self.base("PLANNED_DRILL")
        valid.update(
            affected_sites=[],
            hard_fence_evidence_hash=None,
            change_id="CHG-2026-0042",
            approval_evidence_hash=digest("approved-drill"),
            approvers=["operator-a", "operator-b"],
        )
        self.assert_classification(valid, "ALLOW_PLANNED_DRILL")
        invalid = (
            {**valid, "change_id": None},
            {**valid, "approval_evidence_hash": None},
            {**valid, "approval_evidence_hash": "bad"},
            {**valid, "approvers": ["operator-a", "operator-a"]},
            {**valid, "approvers": ["operator-a"]},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_classification(payload, "NO_GO_UNCLASSIFIED")
        variants = []
        relaxed_common = json.loads(json.dumps(self.policy))
        relaxed_common["common_client"] = "besu"
        variants.append(relaxed_common)
        relaxed_drill = json.loads(json.dumps(self.policy))
        relaxed_drill["planned_drill"]["distinct_approver_count"] = 1
        variants.append(relaxed_drill)
        relaxed_fence = json.loads(json.dumps(self.policy))
        relaxed_fence["site_failure"]["hard_fence_required"] = False
        variants.append(relaxed_fence)
        for index, variant in enumerate(variants):
            with self.subTest(tampered_policy=index):
                policy = self.work / f"tampered-policy-{index}.json"
                policy.write_text(json.dumps(variant), encoding="utf-8")
                self.assert_classification(valid, "NO_GO_UNCLASSIFIED", policy=policy)

    def test_site_failure_never_bypasses_hard_fence(self):
        valid = self.base("SITE_FAILURE")
        self.assert_classification(valid, "ALLOW_SITE_FAILURE_REVIEW")
        for hard_fence in (None, "", "sha256:xyz"):
            with self.subTest(hard_fence=hard_fence):
                payload = {**valid, "hard_fence_evidence_hash": hard_fence}
                self.assert_classification(payload, "NO_GO_UNCLASSIFIED")
        common = {
            **valid,
            "incident_type": "COMMON_CLIENT_FAILURE",
            "primary_execution_client": "nethermind",
            "standby_execution_client": "nethermind",
            "affected_sites": ["primary-aws", "standby-standby"],
        }
        self.assert_classification(common, "NO_GO_COMMON_CLIENT")


if __name__ == "__main__":
    unittest.main()
