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


class TerraformDeployContractTests(unittest.TestCase):
    def test_deploy_workflow_exists(self) -> None:
        self.assertTrue(DEPLOY_WORKFLOW.is_file())

    def test_deploy_plan_role_and_apply_role_are_separated(self) -> None:
        workflow = read(DEPLOY_WORKFLOW)
        self.assertRegex(workflow, r"plan:\n(?:.*\n){0,12}?\s+permissions:\n\s+contents: read\n\s+id-token: write")
        self.assertRegex(workflow, r"apply:\n(?:.*\n){0,14}?\s+needs: plan\n\s+environment: hoodi-testnet-dev")
        self.assertRegex(workflow, r"apply:\n(?:.*\n){0,18}?\s+permissions:\n\s+contents: read\n\s+id-token: write")
        self.assertIn("plan_role_arn", workflow)
        self.assertIn("apply_role_arn", workflow)
        self.assertIn("aws-actions/configure-aws-credentials@", workflow)
        self.assertIn("role-to-assume: ${{ steps.runtime.outputs.plan_role_arn }}", workflow)
        self.assertIn("role-to-assume: ${{ steps.runtime.outputs.apply_role_arn }}", workflow)


    def test_deploy_uses_manual_hoodi_dispatch_and_non_cancelling_concurrency(self) -> None:
        workflow = read(DEPLOY_WORKFLOW)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment:", workflow)
        self.assertIn("hoodi-testnet-dev", workflow)
        self.assertIn("terraform-hoodi-testnet-dev", workflow)
        self.assertRegex(workflow, r"cancel-in-progress:\s*false")
        self.assertIn("TF_IN_AUTOMATION: true", workflow)
        self.assertIn("TF_INPUT: false", workflow)

    def test_exact_plan_metadata_binding_and_standard_apply_are_present(self) -> None:
        workflow = read(DEPLOY_WORKFLOW)
        for required in (
            "workflow_run_id",
            "workflow_run_attempt",
            "commit_sha",
            "environment",
            "aws_account_id",
            "aws_region",
            "terraform_version",
            "provider_lock_sha256",
            "backend_fingerprint",
            "runtime_inputs_sha256",
            "plan_sha256",
            "plan_object_version_id",
            "policy_gate",
            "expires_at",
            "tf.plan",
            "--version-id",
            "terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        self.assertNotIn("terraform apply --plan", workflow)
        self.assertNotRegex(workflow, r"upload-artifact[\s\S]{0,600}tf\.plan")
        self.assertNotRegex(workflow, r"upload-artifact[\s\S]{0,600}plan\.json")

    def test_exact_apply_role_is_bound_from_plan_metadata_to_the_protected_job(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertIn("terraform_apply_role_arn", workflow)
                self.assertIn('--arg apply_role "$EXPECTED_APPLY_ROLE_ARN"', workflow)
                self.assertIn("(.terraform_apply_role_arn == $apply_role)", workflow)

    def test_tracked_runtime_manifest_is_the_only_terraform_input_source(self) -> None:
        self.assertTrue(RUNTIME_MANIFEST.is_file())
        self.assertTrue(RUNTIME_RENDERER.is_file())
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertIn("primary-aws/terraform/ci/runtime-inputs.json", workflow)
                self.assertIn("shared/scripts/render-terraform-ci-runtime.py", workflow)
                self.assertIn("runtime_manifest_sha256", workflow)
                self.assertNotIn("${{ vars.", workflow)
                self.assertNotIn("TF_VAR_", workflow)
                self.assertNotIn("CI_ADMIN_CIDRS_JSON", workflow)
                self.assertNotIn("CI_OPERATOR_ALERT_EMAILS_JSON", workflow)

    def test_manifest_values_bind_credentials_backend_metadata_and_approval_summary(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                for required in (
                    "steps.runtime.outputs.plan_role_arn",
                    "steps.runtime.outputs.apply_role_arn",
                    "steps.runtime.outputs.aws_account_id",
                    "steps.runtime.outputs.aws_region",
                    "steps.runtime.outputs.state_bucket",
                    "steps.runtime.outputs.artifact_bucket",
                    "runtime_manifest_sha256",
                    "Canonical reviewed runtime manifest",
                ):
                    self.assertIn(required, workflow)
                self.assertIn("runtime_manifest_sha256 == $runtime_manifest_sha", workflow)
                self.assertIn("terraform_apply_role_arn == $apply_role", workflow)

    def test_approval_summary_keeps_non_secret_aws_account_evidence_visible(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertNotIn("mask-aws-account-id: true", workflow)
                self.assertGreaterEqual(
                    workflow.count("mask-aws-account-id: false"),
                    2,
                    "plan and protected apply must not register the account ID as a secret",
                )

    def test_fresh_runner_region_and_exact_assumed_role_are_verified_without_shared_config(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertNotIn("aws configure get region", workflow)
                self.assertIn('test "$AWS_REGION" = "$EXPECTED_AWS_REGION"', workflow)
                self.assertIn('test "$AWS_DEFAULT_REGION" = "$EXPECTED_AWS_REGION"', workflow)
                self.assertIn("actual_role_name", workflow)
                self.assertIn("expected_role_name", workflow)

    def test_dispatch_inputs_are_never_interpolated_directly_into_shell(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            shell_blocks = re.findall(r"\n\s+run: \|\n((?:\s{10,}.*\n?)*)", workflow)
            self.assertTrue(shell_blocks, f"no shell blocks found in {workflow_path}")
            for shell_block in shell_blocks:
                with self.subTest(workflow=workflow_path.name):
                    self.assertNotIn("${{ inputs.", shell_block)

    def test_deploy_and_teardown_reject_stale_main_via_authenticated_github_api(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertGreaterEqual(
                    workflow.count("GH_READ_TOKEN: ${{ github.token }}"),
                    2,
                    "both plan and protected apply must authenticate the main-tip read",
                )
                self.assertGreaterEqual(
                    workflow.count('Authorization: Bearer $GH_READ_TOKEN'), 2
                )
                self.assertGreaterEqual(
                    workflow.count(
                        '$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/git/ref/heads/main'
                    ),
                    2,
                )
                self.assertGreaterEqual(
                    workflow.count('test "$GITHUB_SHA" = "$origin_main"'), 2
                )
                self.assertNotIn("git ls-remote origin refs/heads/main", workflow)

    def test_protected_apply_rechecks_main_tip_immediately_before_mutation(self) -> None:
        cases = protected_apply_cases()
        final_expiry_check = (
            "jq -e '(.expires_at | fromdateiso8601) > now' "
            '"$metadata_file" >/dev/null'
        )
        for workflow_path, protected_job_marker in cases:
            protected_job = read(workflow_path).split(protected_job_marker, maxsplit=1)[1]
            final_apply = "terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan"
            with self.subTest(workflow=workflow_path.name):
                self.assertIn("GH_READ_TOKEN: ${{ github.token }}", protected_job)
                self.assertIn('test "$GITHUB_SHA" = "$origin_main"', protected_job)
                self.assertLess(
                    protected_job.rindex("terraform -chdir=primary-aws/terraform init"),
                    protected_job.rindex('test "$GITHUB_SHA" = "$origin_main"'),
                )
                self.assertLess(
                    protected_job.rindex('test "$GITHUB_SHA" = "$origin_main"'),
                    protected_job.rindex(final_expiry_check),
                )
                self.assertLess(
                    protected_job.rindex(final_expiry_check),
                    protected_job.index(final_apply),
                )

    def test_final_expiry_gate_rejects_a_plan_that_expires_during_apply_setup(self) -> None:
        expiry_filter = "(.expires_at | fromdateiso8601) > now"
        for expires_at, expected_returncode in (
            ("1970-01-01T00:00:00Z", 1),
            ("2999-01-01T00:00:00Z", 0),
        ):
            with self.subTest(expires_at=expires_at):
                result = subprocess.run(
                    ["jq", "-e", expiry_filter],
                    input=json.dumps({"expires_at": expires_at}),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, expected_returncode, result.stderr)

    def test_policy_allows_ordinary_computed_unknown_values(self) -> None:
        payload = {
            "resource_changes": [
                {
                    "address": "aws_instance.node",
                    "type": "aws_instance",
                    "change": {
                        "actions": ["create"],
                        "after": {},
                        "after_unknown": {"id": True},
                        "after_sensitive": {},
                    },
                }
            ]
        }
        result = run_jq_filter(PLAN_POLICY, payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

    def test_policy_rejects_unknown_sensitive_values(self) -> None:
        payload = {
            "resource_changes": [
                {
                    "address": "aws_ssm_parameter.secret",
                    "type": "aws_ssm_parameter",
                    "change": {
                        "actions": ["create"],
                        "after": {},
                        "after_unknown": {"value": True},
                        "after_sensitive": {"value": True},
                    },
                }
            ]
        }
        result = run_jq_filter(PLAN_POLICY, payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.strip(), "false")

    def test_policy_rejects_unknown_value_under_sensitive_parent(self) -> None:
        payload = {
            "resource_changes": [
                {
                    "address": "example_secret.nested",
                    "type": "example_secret",
                    "change": {
                        "actions": ["create"],
                        "after": {},
                        "after_unknown": {"secret_object": {"value": True}},
                        "after_sensitive": {"secret_object": True},
                    },
                }
            ]
        }
        result = run_jq_filter(PLAN_POLICY, payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.strip(), "false")

    def test_policy_rejects_public_ingress_for_current_rule_resources(self) -> None:
        fixtures = (
            ("cidr_ipv4", "0.0.0.0/0"),
            ("cidr_ipv6", "::/0"),
        )
        for attribute, value in fixtures:
            payload = {
                "resource_changes": [
                    {
                        "address": "aws_vpc_security_group_ingress_rule.public",
                        "type": "aws_vpc_security_group_ingress_rule",
                        "change": {
                            "actions": ["create"],
                            "after": {attribute: value},
                            "after_unknown": {},
                            "after_sensitive": {},
                        },
                    }
                ]
            }
            result = run_jq_filter(PLAN_POLICY, payload)
            with self.subTest(attribute=attribute):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.strip(), "false")

    def test_policy_allows_only_the_exact_declared_public_p2p_rules(self) -> None:
        fixtures = (
            ("el_p2p_tcp", "tcp", 30303),
            ("el_p2p_udp", "udp", 30303),
            ("cl_p2p_tcp", "tcp", 9000),
            ("cl_p2p_udp", "udp", 9000),
            ("cl_quic_udp", "udp", 9001),
        )
        for name, protocol, port in fixtures:
            payload = {
                "resource_changes": [
                    {
                        "address": f"aws_vpc_security_group_ingress_rule.{name}",
                        "type": "aws_vpc_security_group_ingress_rule",
                        "change": {
                            "actions": ["create"],
                            "after": {
                                "cidr_ipv4": "0.0.0.0/0",
                                "from_port": port,
                                "to_port": port,
                                "ip_protocol": protocol,
                            },
                            "after_unknown": {},
                            "after_sensitive": {},
                        },
                    }
                ]
            }
            result = run_jq_filter(PLAN_POLICY, payload)
            with self.subTest(name=name):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "true")

    def test_policy_rejects_a_public_rule_that_reuses_a_p2p_name_with_the_wrong_tuple(self) -> None:
        mutations = (
            {"from_port": 22, "to_port": 22, "ip_protocol": "tcp", "cidr_ipv4": "0.0.0.0/0"},
            {"from_port": 30303, "to_port": 30304, "ip_protocol": "tcp", "cidr_ipv4": "0.0.0.0/0"},
            {"from_port": 30303, "to_port": 30303, "ip_protocol": "udp", "cidr_ipv4": "0.0.0.0/0"},
            {"from_port": 30303, "to_port": 30303, "ip_protocol": "tcp", "cidr_ipv6": "::/0"},
        )
        for after in mutations:
            payload = {
                "resource_changes": [
                    {
                        "address": "aws_vpc_security_group_ingress_rule.el_p2p_tcp",
                        "type": "aws_vpc_security_group_ingress_rule",
                        "change": {
                            "actions": ["create"],
                            "after": after,
                            "after_unknown": {},
                            "after_sensitive": {},
                        },
                    }
                ]
            }
            result = run_jq_filter(PLAN_POLICY, payload)
            with self.subTest(after=after):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.strip(), "false")

    def test_policy_allows_only_canonical_ipv4_host_cidrs_for_ssh(self) -> None:
        allowed = {
            "resource_changes": [
                {
                    "address": 'aws_vpc_security_group_ingress_rule.ssh_admin["203.0.113.10/32"]',
                    "type": "aws_vpc_security_group_ingress_rule",
                    "change": {
                        "actions": ["create"],
                        "after": {
                            "cidr_ipv4": "203.0.113.10/32",
                            "from_port": 22,
                            "to_port": 22,
                            "ip_protocol": "tcp",
                        },
                        "after_unknown": {},
                        "after_sensitive": {},
                    },
                }
            ]
        }
        allowed_result = run_jq_filter(PLAN_POLICY, allowed)
        self.assertEqual(allowed_result.returncode, 0, allowed_result.stderr)

        rejected = ("0.0.0.0/1", "128.0.0.0/1", "203.0.113.0/24", "203.000.113.010/32", "::/0")
        for cidr in rejected:
            attribute = "cidr_ipv6" if ":" in cidr else "cidr_ipv4"
            payload = json.loads(json.dumps(allowed))
            payload["resource_changes"][0]["change"]["after"].pop("cidr_ipv4")
            payload["resource_changes"][0]["change"]["after"][attribute] = cidr
            result = run_jq_filter(PLAN_POLICY, payload)
            with self.subTest(cidr=cidr):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.strip(), "false")

    def test_policy_binds_ssh_and_wireguard_to_the_reviewed_runtime_manifest(self) -> None:
        def ingress(address: str, cidr: str, protocol: str, port: int) -> dict[str, object]:
            return {
                "resource_changes": [
                    {
                        "address": address,
                        "type": "aws_vpc_security_group_ingress_rule",
                        "change": {
                            "actions": ["create"],
                            "after": {
                                "cidr_ipv4": cidr,
                                "from_port": port,
                                "to_port": port,
                                "ip_protocol": protocol,
                            },
                            "after_unknown": {},
                            "after_sensitive": {},
                        },
                    }
                ]
            }

        reviewed_ssh = ingress(
            'aws_vpc_security_group_ingress_rule.ssh_admin["203.0.113.10/32"]',
            "203.0.113.10/32",
            "tcp",
            22,
        )
        wrong_ssh = ingress(
            'aws_vpc_security_group_ingress_rule.ssh_admin["8.8.8.8/32"]',
            "8.8.8.8/32",
            "tcp",
            22,
        )
        reviewed_wireguard = ingress(
            'aws_vpc_security_group_ingress_rule.wireguard_peer["peer"]',
            "198.51.100.20/32",
            "udp",
            51820,
        )
        wrong_wireguard = ingress(
            'aws_vpc_security_group_ingress_rule.wireguard_peer["peer"]',
            "1.1.1.1/32",
            "udp",
            51820,
        )

        self.assertEqual(run_jq_filter(PLAN_POLICY, reviewed_ssh).returncode, 0)
        self.assertNotEqual(run_jq_filter(PLAN_POLICY, wrong_ssh).returncode, 0)
        self.assertEqual(run_jq_filter(PLAN_POLICY, reviewed_wireguard).returncode, 0)
        self.assertNotEqual(run_jq_filter(PLAN_POLICY, wrong_wireguard).returncode, 0)
        self.assertNotEqual(
            run_jq_filter(PLAN_POLICY, reviewed_wireguard, backup_peer=None).returncode,
            0,
        )

        workflow = read(DEPLOY_WORKFLOW)
        self.assertIn('--argjson admin_cidrs "$admin_cidrs"', workflow)
        self.assertIn('--argjson backup_peer "$backup_peer"', workflow)

    def test_null_backup_peer_is_a_valid_policy_input(self) -> None:
        workflow = read(DEPLOY_WORKFLOW)
        self.assertNotIn(
            'jq -c -e .backup_peer_public_ip',
            workflow,
            "jq -e treats the valid null value as an execution failure",
        )
        self.assertIn(
            'if has("backup_peer_public_ip") then .backup_peer_public_ip else error("missing backup_peer_public_ip") end',
            workflow,
        )
        extraction = subprocess.run(
            [
                "jq",
                "-c",
                'if has("backup_peer_public_ip") then .backup_peer_public_ip else error("missing backup_peer_public_ip") end',
            ],
            input='{"backup_peer_public_ip":null}',
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(extraction.returncode, 0, extraction.stderr)
        self.assertEqual(extraction.stdout.strip(), "null")

    def test_false_staging_flag_is_valid_in_both_teardown_stages(self) -> None:
        expression = (
            'if (has("enable_staging_bucket") and '
            '((.enable_staging_bucket | type) == "boolean")) then '
            '.enable_staging_bucket else error("missing or invalid enable_staging_bucket") end'
        )
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertNotIn(
                    'jq -er .enable_staging_bucket',
                    workflow,
                    "jq -e treats the valid false value as an execution failure",
                )
                self.assertIn(expression, workflow)

        extraction = subprocess.run(
            ["jq", "-c", expression],
            input='{"enable_staging_bucket":false}',
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(extraction.returncode, 0, extraction.stderr)
        self.assertEqual(extraction.stdout.strip(), "false")

    def test_policy_rejects_numeric_tcp_and_unknown_ssh_fields(self) -> None:
        numeric_tcp = {
            "resource_changes": [
                {
                    "address": 'aws_vpc_security_group_ingress_rule.ssh_admin["0.0.0.0/1"]',
                    "type": "aws_vpc_security_group_ingress_rule",
                    "change": {
                        "actions": ["create"],
                        "after": {
                            "cidr_ipv4": "0.0.0.0/1",
                            "from_port": 22,
                            "to_port": 22,
                            "ip_protocol": "6",
                        },
                        "after_unknown": {},
                        "after_sensitive": {},
                    },
                }
            ]
        }
        result = run_jq_filter(PLAN_POLICY, numeric_tcp)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.strip(), "false")

        for unknown_field in ("cidr_ipv4", "cidr_ipv6", "from_port", "to_port", "ip_protocol"):
            payload = json.loads(json.dumps(numeric_tcp))
            payload["resource_changes"][0]["change"]["after"].pop(unknown_field, None)
            payload["resource_changes"][0]["change"]["after_unknown"] = {unknown_field: True}
            result = run_jq_filter(PLAN_POLICY, payload)
            with self.subTest(unknown_field=unknown_field):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.strip(), "false")

    def test_policy_rejects_legacy_and_inline_ingress_schemas(self) -> None:
        fixtures = (
            {
                "address": "aws_security_group_rule.legacy_ssh",
                "type": "aws_security_group_rule",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "type": "ingress",
                        "protocol": "6",
                        "from_port": 22,
                        "to_port": 22,
                        "cidr_blocks": ["0.0.0.0/1"],
                    },
                    "after_unknown": {},
                    "after_sensitive": {},
                },
            },
            {
                "address": "aws_security_group.inline",
                "type": "aws_security_group",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "ingress": [
                            {
                                "protocol": "tcp",
                                "from_port": 8545,
                                "to_port": 8545,
                                "cidr_blocks": ["0.0.0.0/1"],
                            }
                        ]
                    },
                    "after_unknown": {},
                    "after_sensitive": {},
                },
            },
        )
        for resource in fixtures:
            result = run_jq_filter(PLAN_POLICY, {"resource_changes": [resource]})
            with self.subTest(address=resource["address"]):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.strip(), "false")

    def test_sanitized_summary_contains_only_counts_addresses_and_actions(self) -> None:
        payload = {
            "resource_changes": [
                {
                    "address": "aws_instance.node",
                    "type": "aws_instance",
                    "change": {
                        "actions": ["create"],
                        "before": None,
                        "after": {"user_data": "DO_NOT_LEAK", "instance_type": "m7g.large"},
                    },
                },
                {
                    "address": "aws_security_group.node",
                    "type": "aws_security_group",
                    "change": {
                        "actions": ["update"],
                        "before": {"description": "OLD_SECRET"},
                        "after": {"description": "NEW_SECRET"},
                    },
                },
            ]
        }
        result = run_jq_filter(PLAN_SUMMARY, payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(
            summary,
            {
                "counts": {"create": 1, "update": 1, "delete": 0, "replace": 0, "read": 0},
                "changes": [
                    {"address": "aws_instance.node", "actions": ["create"]},
                    {"address": "aws_security_group.node", "actions": ["update"]},
                ],
            },
        )
        self.assertNotIn("DO_NOT_LEAK", result.stdout)
        self.assertNotIn("SECRET", result.stdout)

    def test_sanitized_summary_redacts_for_each_keys_from_resource_addresses(self) -> None:
        payload = {
            "resource_changes": [
                {
                    "address": 'aws_sns_topic_subscription.operators["operator@example.com"]',
                    "type": "aws_sns_topic_subscription",
                    "change": {"actions": ["create"], "before": None, "after": {}},
                },
                {
                    "address": 'aws_vpc_security_group_ingress_rule.ssh_admin["203.0.113.10/32"]',
                    "type": "aws_vpc_security_group_ingress_rule",
                    "change": {"actions": ["create"], "before": None, "after": {}},
                },
                {
                    "address": 'example_resource.quoted["secret\\\"key@example.com"]',
                    "type": "example_resource",
                    "change": {"actions": ["create"], "before": None, "after": {}},
                },
            ]
        }
        result = run_jq_filter(PLAN_SUMMARY, payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(
            [change["address"] for change in summary["changes"]],
            [
                'aws_sns_topic_subscription.operators["<redacted-key>"]',
                'aws_vpc_security_group_ingress_rule.ssh_admin["<redacted-key>"]',
                'example_resource.quoted["<redacted-key>"]',
            ],
        )
        self.assertNotIn("operator@example.com", result.stdout)
        self.assertNotIn("203.0.113.10", result.stdout)
        self.assertNotIn("secret", result.stdout)

    def test_approval_evidence_is_published_before_each_protected_apply_job(self) -> None:
        cases = protected_apply_cases()
        for workflow_path, protected_job_marker in cases:
            workflow = read(workflow_path)
            plan_job, apply_job = workflow.split(protected_job_marker, maxsplit=1)
            with self.subTest(workflow=workflow_path.name):
                for required in (
                    "plan_sha256: ${{ steps.publish.outputs.plan_sha256 }}",
                    "plan_expires_at: ${{ steps.publish.outputs.plan_expires_at }}",
                    'echo "plan_sha256=$(jq -er .plan_sha256',
                    'echo "plan_expires_at=$(jq -er .expires_at',
                    r"Plan SHA-256: \`${{ steps.publish.outputs.plan_sha256 }}\`",
                    r"S3 plan VersionId: \`${{ steps.publish.outputs.plan_object_version_id }}\`",
                    r"Expires at: \`${{ steps.publish.outputs.plan_expires_at }}\`",
                    r"Runtime input SHA-256: \`${{ steps.publish.outputs.runtime_inputs_sha256 }}\`",
                    r"Commit: \`$GITHUB_SHA\`; run: \`$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT\`",
                    "Action counts:",
                    "Resource address",
                ):
                    self.assertIn(required, plan_job)
                self.assertIn("plan-summary.jq", plan_job)
                self.assertNotIn("plan-summary.jq", apply_job)

                summary_step = plan_job.split("Publish sanitized", maxsplit=1)[1]
                self.assertNotIn("${{ secrets.", summary_step)
                self.assertNotIn("${{ vars.", summary_step)

    def test_protected_reviewer_must_open_the_same_run_full_plan_diff(self) -> None:
        for workflow_path in pipeline_workflow_paths():
            workflow = read(workflow_path)
            plan_index = workflow.index("-out=tf.plan")
            review_step = workflow.index(
                "- name: Render reviewer-visible Terraform plan diff",
                plan_index,
            )
            show_index = workflow.index(
                'terraform -chdir=primary-aws/terraform show -no-color "$plan_file"',
                review_step,
            )
            publish_index = workflow.index("Publish sanitized", show_index)
            with self.subTest(workflow=workflow_path.name):
                self.assertEqual(
                    [plan_index, review_step, show_index, publish_index],
                    sorted([plan_index, review_step, show_index, publish_index]),
                )
                review_block = workflow[review_step:publish_index]
                self.assertNotIn("GITHUB_STEP_SUMMARY", review_block)
                self.assertNotIn("upload-artifact", review_block)
                self.assertIn(
                    "Do not approve from the address/action table alone",
                    workflow,
                )

    def test_deploy_fails_closed_before_apply(self) -> None:
        workflow = read(DEPLOY_WORKFLOW)
        for required in (
            "git merge-base --is-ancestor",
            "refs/heads/main",
            "aws sts get-caller-identity",
            "AWS_REGION",
            "wrong AWS region",
            "-lockfile=readonly",
            "terraform plan -input=false -lock-timeout=5m -out=tf.plan",
            "terraform show -json tf.plan",
            "plan-policy.jq",
            "sha256sum",
            "terraform apply -input=false -lock-timeout=5m tf.plan",
            "metadata binding mismatch",
            "expired plan metadata",
            "re-run failed jobs is forbidden",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        self.assertLess(
            workflow.index("metadata binding mismatch"),
            workflow.index("terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan"),
        )

    def test_ci_runtime_files_are_renderer_owned_not_handwritten_examples(self) -> None:
        self.assertTrue((ROOT / ".terraform-version").is_file())
        self.assertEqual((ROOT / ".terraform-version").read_text(encoding="utf-8").strip(), "1.15.9")
        self.assertTrue((ROOT / "primary-aws/terraform/ci/plan-policy.jq").is_file())
        self.assertTrue((ROOT / "primary-aws/terraform/ci/plan-summary.jq").is_file())
        self.assertTrue(RUNTIME_MANIFEST.is_file())
        self.assertTrue(RUNTIME_RENDERER.is_file())
        for obsolete in (
            ROOT / "primary-aws/terraform/ci/backend.hcl.example",
            ROOT / "primary-aws/terraform/ci/variables.example.json",
        ):
            with self.subTest(obsolete=obsolete.name):
                self.assertFalse(obsolete.exists())


if __name__ == "__main__":
    unittest.main()

    def test_saved_plan_keys_begin_with_the_repository_scope(self) -> None:
        deploy = read(DEPLOY_WORKFLOW)
        self.assertIn(
            'plan_object_key="$GITHUB_REPOSITORY/hoodi-testnet-dev/$GITHUB_SHA/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT/tf.plan"',
            deploy,
        )


if __name__ == "__main__":
    unittest.main()
