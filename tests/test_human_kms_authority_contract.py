from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OP1_POLICY = ROOT / "primary-aws/bootstrap/operator-1-builder.json"
OP2_POLICY = ROOT / "primary-aws/bootstrap/operator-2-approver.json"
KMS_TF = ROOT / "primary-aws/terraform/kms.tf"

EXPECTED_TAG_CONDITIONS = {
    "kms:CallerAccount": "${aws:PrincipalAccount}",
    "aws:ResourceTag/Project": "eth-failover",
    "aws:ResourceTag/Network": "hoodi",
    "aws:ResourceTag/Managed": "terraform",
}


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def statements(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return policy["Statement"]


def actions(statement: dict[str, Any]) -> set[str]:
    raw = statement.get("Action", [])
    return {raw} if isinstance(raw, str) else set(raw)


def all_allowed_actions(policy: dict[str, Any], prefix: str) -> set[str]:
    result: set[str] = set()
    for statement in statements(policy):
        if statement.get("Effect") != "Allow":
            continue
        result.update(action for action in actions(statement) if action.startswith(prefix))
    return result


def by_sid(policy: dict[str, Any], sid: str) -> dict[str, Any]:
    matches = [item for item in statements(policy) if item.get("Sid") == sid]
    if len(matches) != 1:
        raise AssertionError(f"expected one {sid!r} statement, found {len(matches)}")
    return matches[0]


class HumanKmsAuthorityContractTests(unittest.TestCase):
    def test_human_permission_sets_have_no_iam_or_kms_administration(self) -> None:
        """Re-adding direct human infrastructure mutation must fail this boundary."""
        forbidden_iam = {
            "iam:CreateRole",
            "iam:DeleteRole",
            "iam:PassRole",
            "iam:PutRolePolicy",
            "iam:AttachRolePolicy",
            "iam:CreateInstanceProfile",
            "iam:AddRoleToInstanceProfile",
        }
        forbidden_kms = {
            "kms:CreateKey",
            "kms:PutKeyPolicy",
            "kms:ScheduleKeyDeletion",
            "kms:DisableKey",
            "kms:EnableKey",
            "kms:TagResource",
            "kms:UntagResource",
            "kms:CreateGrant",
        }
        for path in (OP1_POLICY, OP2_POLICY):
            with self.subTest(policy=path.name):
                policy = load_policy(path)
                self.assertFalse(forbidden_iam & all_allowed_actions(policy, "iam:"))
                self.assertFalse(forbidden_kms & all_allowed_actions(policy, "kms:"))

    def test_operator_1_can_only_describe_and_seal_tagged_recovery_keys(self) -> None:
        """Operator 1 must never gain decrypt or data-key generation by policy drift."""
        policy = load_policy(OP1_POLICY)
        self.assertEqual(
            {"kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"},
            all_allowed_actions(policy, "kms:"),
        )
        seal = by_sid(policy, "SealTaggedRecoveryKey")
        self.assertEqual({"kms:Encrypt"}, actions(seal))
        self.assertEqual("arn:aws:kms:*:*:key/*", seal["Resource"])
        conditions = seal["Condition"]["StringEquals"]
        self.assertEqual(EXPECTED_TAG_CONDITIONS.items() <= conditions.items(), True)
        self.assertEqual("recovery-keystore-envelope", conditions["aws:ResourceTag/Purpose"])
        staging = by_sid(policy, "GenerateOnlyStagingDataKeyViaS3")
        self.assertEqual({"kms:GenerateDataKey"}, actions(staging))
        self.assertEqual("arn:aws:kms:ap-northeast-2:*:key/*", staging["Resource"])
        self.assertEqual(
            "s3.ap-northeast-2.amazonaws.com",
            staging["Condition"]["StringEquals"]["kms:ViaService"],
        )
        self.assertEqual(
            "arn:aws:s3:::eth-failover-hoodi-staging-${aws:PrincipalAccount}",
            staging["Condition"]["StringEquals"]["kms:EncryptionContext:aws:s3:arn"],
        )

    def test_operator_2_can_only_describe_and_unseal_tagged_recovery_keys(self) -> None:
        """Operator 2 must never gain encrypt or data-key generation by policy drift."""
        policy = load_policy(OP2_POLICY)
        self.assertEqual(
            {"kms:Decrypt", "kms:DescribeKey"},
            all_allowed_actions(policy, "kms:"),
        )
        unseal = by_sid(policy, "UnsealTaggedRecoveryKey")
        self.assertEqual({"kms:Decrypt"}, actions(unseal))
        self.assertEqual("arn:aws:kms:*:*:key/*", unseal["Resource"])
        conditions = unseal["Condition"]["StringEquals"]
        self.assertEqual(EXPECTED_TAG_CONDITIONS.items() <= conditions.items(), True)
        self.assertEqual("recovery-keystore-envelope", conditions["aws:ResourceTag/Purpose"])

    def test_describe_permission_is_account_and_tag_fail_closed(self) -> None:
        """A same-name key in another account or an untagged key must remain invisible."""
        for path in (OP1_POLICY, OP2_POLICY):
            with self.subTest(policy=path.name):
                describe = by_sid(load_policy(path), "DescribeTaggedHoodiKeys")
                self.assertEqual({"kms:DescribeKey"}, actions(describe))
                self.assertEqual("arn:aws:kms:*:*:key/*", describe["Resource"])
                conditions = describe["Condition"]["StringEquals"]
                self.assertEqual(EXPECTED_TAG_CONDITIONS, conditions)

    def test_kms_key_policies_separate_routine_apply_from_stable_break_glass(self) -> None:
        """Removing the recovery route or turning it into routine SSO admin must fail."""
        kms = KMS_TF.read_text(encoding="utf-8")
        self.assertNotIn("EnableAccountRootPermissions", kms)
        self.assertNotIn("AllowKeyAdministrators", kms)
        self.assertNotIn('actions   = ["kms:*"]', kms)
        self.assertEqual(2, kms.count('sid       = "AllowTerraformApplyRoleAdministration"'))
        self.assertEqual(2, kms.count("identifiers = [var.terraform_apply_role_arn]"))
        self.assertEqual(2, kms.count('sid       = "AllowStableKmsBreakGlassRoleAdministration"'))
        self.assertEqual(2, kms.count('test     = "ArnEquals"'))
        self.assertEqual(2, kms.count("values   = [var.kms_break_glass_role_arn]"))
        self.assertEqual(2, kms.count('identifiers = ["*"]'))
        self.assertNotIn("local.sso_operator_role_arn_patterns", kms)

        variables = (ROOT / "primary-aws/terraform/variables-kms.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn('variable "terraform_apply_role_arn"', variables)
        self.assertIn('variable "kms_break_glass_role_arn"', variables)
        self.assertIn("nullable    = false", variables)
        self.assertIn("split(\":\", var.terraform_apply_role_arn)[4]", variables)
        self.assertNotIn("role/hoodi-testnet-dev-TerraformApplyRole$", variables)

    def test_primary_key_allows_only_operator_1_s3_staging_data_key_generation(self) -> None:
        """Direct GenerateDataKey or recovery-key staging authority must fail this boundary."""
        kms = KMS_TF.read_text(encoding="utf-8")
        self.assertEqual(1, kms.count('sid       = "AllowOperator1StagingEnvelopeViaS3"'))
        self.assertEqual(
            1,
            kms.count('actions   = ["kms:GenerateDataKey", "kms:DescribeKey"]'),
        )
        self.assertIn('variable = "kms:ViaService"', kms)
        self.assertIn('values   = ["s3.${var.region}.amazonaws.com"]', kms)
        self.assertIn('variable = "kms:EncryptionContext:aws:s3:arn"', kms)
        self.assertIn(
            'values   = ["arn:${data.aws_partition.current.partition}:s3:::${local.project}-${var.network}-staging-${data.aws_caller_identity.current.account_id}"]',
            kms,
        )

    def test_kms_key_policies_allow_only_exact_plan_role_refresh_reads(self) -> None:
        """A subsequent CI plan must refresh keys without gaining mutation authority."""
        kms = KMS_TF.read_text(encoding="utf-8")
        self.assertEqual(2, kms.count('sid       = "AllowTerraformPlanRoleReadOnly"'))
        self.assertEqual(2, kms.count("identifiers = [var.terraform_plan_role_arn]"))
        self.assertEqual(
            2,
            kms.count(
                'actions   = ["kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags"]'
            ),
        )
        variables = (ROOT / "primary-aws/terraform/variables-kms.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn('variable "terraform_plan_role_arn"', variables)
        self.assertIn(
            "split(\":\", var.terraform_plan_role_arn)[4] == split(\":\", var.node_permissions_boundary_arn)[4]",
            variables,
        )

    def test_recovery_key_policy_splits_operator_1_seal_from_operator_2_unseal(self) -> None:
        """Combining both operators into one crypto statement must fail this contract."""
        kms = KMS_TF.read_text(encoding="utf-8")
        self.assertIn('sid       = "AllowOperator1SealRecoveryKey"', kms)
        self.assertIn('actions   = ["kms:Encrypt", "kms:DescribeKey"]', kms)
        self.assertIn("values   = [local.kms_seal_operator_role_arn_pattern]", kms)
        self.assertIn('sid       = "AllowOperator2UnsealRecoveryKey"', kms)
        self.assertIn('actions   = ["kms:Decrypt", "kms:DescribeKey"]', kms)
        self.assertIn("values   = [local.kms_unseal_operator_role_arn_pattern]", kms)
        self.assertNotIn("kms:ReEncrypt", kms)
        recovery_policy = kms.split('data "aws_iam_policy_document" "recovery" {', 1)[1]
        self.assertNotIn("AllowOperator1StagingEnvelopeViaS3", recovery_policy)

    def test_recovery_key_has_explicit_authorization_tags(self) -> None:
        """Removing an ABAC input tag from the managed key must fail before release."""
        kms = KMS_TF.read_text(encoding="utf-8")
        self.assertIn('Purpose = "recovery-keystore-envelope"', kms)
        self.assertIn("tags = merge(local.common_tags", kms)


if __name__ == "__main__":
    unittest.main()
