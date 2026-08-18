from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from _cicd_lib import (
    ROOT,
    DEPLOY_WORKFLOW,
    TEARDOWN_WORKFLOW,
    PLAN_POLICY,
    PLAN_SUMMARY,
    TEARDOWN_PREP_POLICY,
    TEARDOWN_POLICY,
    SECURITY_GROUPS,
    RUNTIME_MANIFEST,
    RUNTIME_RENDERER,
    read,
    run_jq_filter,
    run_jq_filter_with_staging,
    pipeline_workflow_paths,
    protected_apply_cases,
)


class TerraformTeardownContractTests(unittest.TestCase):
    def test_all_pipeline_workflows_exist(self) -> None:
        for workflow in (DEPLOY_WORKFLOW, TEARDOWN_WORKFLOW):
            with self.subTest(workflow=workflow.name):
                self.assertTrue(workflow.is_file())

    def test_teardown_plan_and_apply_guards_match_deploy(self) -> None:
        teardown = read(TEARDOWN_WORKFLOW)
        self.assertIn('wrong PlanRole', teardown)
        self.assertIn('wrong ApplyRole', teardown)
        self.assertGreaterEqual(teardown.count('test "$(jq -r .Account <<<"$caller")" = "$EXPECTED_AWS_ACCOUNT_ID"'), 2)
        self.assertNotIn("aws configure get region", teardown)
        self.assertGreaterEqual(teardown.count('test "$AWS_REGION" = "$EXPECTED_AWS_REGION"'), 2)
        self.assertIn(
            'plan_object_key="$GITHUB_REPOSITORY/hoodi-testnet-dev-teardown/$GITHUB_SHA/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT/tf.plan"',
            teardown,
        )

    def test_boundary_suite_ships_with_the_teardown_slice(self) -> None:
        self.assertTrue((ROOT / "shared/tests/test-terraform-cicd-boundary.sh").is_file())

    def test_teardown_requires_a_separately_approved_protected_destroy_preparation(self) -> None:
        deploy = read(DEPLOY_WORKFLOW)
        teardown = read(TEARDOWN_WORKFLOW)
        for required in (
            "prepare_teardown:",
            "type: boolean",
            "PREPARE_TEARDOWN",
            "allow_protected_destroy",
            "TEARDOWN_PREP",
        ):
            with self.subTest(workflow="deploy", required=required):
                self.assertIn(required, deploy)
        for required in (
            "allow_protected_destroy:true",
            "protected destroy preparation is absent",
            "disable_api_termination == false",
            "force_destroy == true",
            "TEARDOWN_PASS",
        ):
            with self.subTest(workflow="teardown", required=required):
                self.assertIn(required, teardown)

    def test_teardown_preparation_policy_allows_only_the_two_state_opt_ins(self) -> None:
        self.assertTrue(TEARDOWN_PREP_POLICY.is_file())
        valid_changes = [
            {
                "address": "aws_instance.node",
                "type": "aws_instance",
                "change": {
                    "actions": ["update"],
                    "before": {"disable_api_termination": True},
                    "after": {"disable_api_termination": False},
                },
            },
            {
                "address": "aws_s3_bucket.staging[0]",
                "type": "aws_s3_bucket",
                "change": {
                    "actions": ["update"],
                    "before": {"force_destroy": False},
                    "after": {"force_destroy": True},
                },
            },
        ]
        valid = {"resource_changes": valid_changes}
        result = run_jq_filter_with_staging(TEARDOWN_PREP_POLICY, valid)
        self.assertEqual(result.returncode, 0, result.stderr)

        # Terraform persists each successful resource update even when a later
        # operation in the same saved-plan apply fails. A new approved
        # preparation run must therefore accept one protected resource whose
        # destroy opt-in is already persisted while transitioning only the other.
        already_prepared = {
            "aws_instance.node": {"disable_api_termination": False},
            "aws_s3_bucket.staging[0]": {"force_destroy": True},
        }
        for already_prepared_address, prepared_values in already_prepared.items():
            resumed = json.loads(json.dumps(valid))
            target = next(
                item
                for item in resumed["resource_changes"]
                if item["address"] == already_prepared_address
            )
            target["change"] = {
                "actions": ["no-op"],
                "before": dict(prepared_values),
                "after": dict(prepared_values),
                "after_unknown": {},
            }
            result = run_jq_filter_with_staging(TEARDOWN_PREP_POLICY, resumed)
            with self.subTest(resume_after=already_prepared_address):
                self.assertEqual(result.returncode, 0, result.stderr)

        mutations = (
            {"resource_changes": []},
            {"resource_changes": [{**valid_changes[0], "change": {"actions": ["create"], "before": None, "after": {"disable_api_termination": False}}}, valid_changes[1]]},
            {"resource_changes": [*valid_changes, {"address": "aws_eip.node", "type": "aws_eip", "change": {"actions": ["update"], "before": {}, "after": {}}}]},
            {"resource_changes": [valid_changes[0]]},
            {
                "resource_changes": [
                    {
                        **valid_changes[0],
                        "change": {
                            "actions": ["update"],
                            "before": {"disable_api_termination": True, "instance_type": "m7i.2xlarge"},
                            "after": {"disable_api_termination": False, "instance_type": "m7i.4xlarge"},
                            "after_unknown": {},
                        },
                    },
                    valid_changes[1],
                ]
            },
            {
                "resource_changes": [
                    valid_changes[0],
                    {
                        **valid_changes[1],
                        "change": {
                            "actions": ["update"],
                            "before": {"force_destroy": False, "bucket": "expected"},
                            "after": {"force_destroy": True, "bucket": "unexpected"},
                            "after_unknown": {},
                        },
                    },
                ]
            },
        )
        for payload in mutations:
            result = run_jq_filter_with_staging(TEARDOWN_PREP_POLICY, payload)
            with self.subTest(payload=payload):
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_teardown_policy_requires_prepared_state_in_the_destroy_plan(self) -> None:
        self.assertTrue(TEARDOWN_POLICY.is_file())
        valid = {
            "resource_changes": [
                {
                    "address": "aws_instance.node",
                    "type": "aws_instance",
                    "change": {"actions": ["delete"], "before": {"disable_api_termination": False}, "after": None},
                },
                {
                    "address": "aws_s3_bucket.staging[0]",
                    "type": "aws_s3_bucket",
                    "change": {"actions": ["delete"], "before": {"force_destroy": True}, "after": None},
                },
            ]
        }
        result = run_jq_filter_with_staging(TEARDOWN_POLICY, valid)
        self.assertEqual(result.returncode, 0, result.stderr)

        # A previous exact-plan apply can persist successful deletes before a
        # later provider operation fails. A new approved teardown run must be
        # able to destroy only the resources that remain, without requiring
        # already-absent protected resources to reappear in state.
        for removed_address in (
            "aws_instance.node",
            "aws_s3_bucket.staging[0]",
        ):
            resumed = json.loads(json.dumps(valid))
            resumed["resource_changes"] = [
                item
                for item in resumed["resource_changes"]
                if item["address"] != removed_address
            ]
            resumed["resource_changes"].append(
                {
                    "address": "aws_sns_topic.alerts",
                    "type": "aws_sns_topic",
                    "change": {
                        "actions": ["delete"],
                        "before": {"name": "eth-failover-hoodi-alerts"},
                        "after": None,
                    },
                }
            )
            result = run_jq_filter_with_staging(TEARDOWN_POLICY, resumed)
            with self.subTest(resume_without=removed_address):
                self.assertEqual(result.returncode, 0, result.stderr)

        both_absent_resume = {
            "resource_changes": [
                {
                    "address": "aws_sns_topic.alerts",
                    "type": "aws_sns_topic",
                    "change": {
                        "actions": ["delete"],
                        "before": {"name": "eth-failover-hoodi-alerts"},
                        "after": None,
                    },
                }
            ]
        }
        result = run_jq_filter_with_staging(TEARDOWN_POLICY, both_absent_resume)
        self.assertEqual(result.returncode, 0, result.stderr)

        unprepared_values = {
            "aws_instance.node": {"disable_api_termination": True},
            "aws_s3_bucket.staging[0]": {"force_destroy": False},
        }
        for address, unprepared in unprepared_values.items():
            payload = json.loads(json.dumps(valid))
            target = next(item for item in payload["resource_changes"] if item["address"] == address)
            target["change"]["before"].update(unprepared)
            result = run_jq_filter_with_staging(TEARDOWN_POLICY, payload)
            with self.subTest(address=address):
                self.assertEqual(result.returncode, 1, result.stderr)

        no_remaining_changes = {"resource_changes": []}
        result = run_jq_filter_with_staging(TEARDOWN_POLICY, no_remaining_changes)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_teardown_workflow_allows_absent_but_not_unprepared_protected_state(self) -> None:
        teardown = read(TEARDOWN_WORKFLOW)
        self.assertIn("protected resource state is unprepared", teardown)
        self.assertIn("($nodes | length) == 0 or", teardown)
        self.assertIn("($staging | length) == 0 or", teardown)
        self.assertNotIn(
            "force_destroy == true must already be persisted in state",
            teardown,
        )

    def test_teardown_requires_distinct_environment_and_typed_confirmation(self) -> None:
        workflow = read(TEARDOWN_WORKFLOW)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("hoodi-testnet-dev-teardown", workflow)
        self.assertIn("hoodi-testnet-dev", workflow)
        self.assertIn("typed confirmation", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform plan -destroy -input=false -lock-timeout=5m -out=tf.plan", workflow)
        self.assertIn("terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan", workflow)


if __name__ == "__main__":
    unittest.main()
