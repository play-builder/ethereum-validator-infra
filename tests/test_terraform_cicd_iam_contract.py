from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "primary-aws/bootstrap/cicd/template.yaml"
PLAN_POLICY = ROOT / "primary-aws/bootstrap/cicd/policies/terraform-plan-role.json"
APPLY_POLICY = ROOT / "primary-aws/bootstrap/cicd/policies/terraform-apply-role.json"
BOUNDARY_POLICY = ROOT / "primary-aws/bootstrap/cicd/policies/node-role-boundary.json"
BOOTSTRAP_README = ROOT / "primary-aws/bootstrap/cicd/README.md"
BOOTSTRAP_PARAMETERS = ROOT / "primary-aws/bootstrap/cicd/parameters.example.json"
IAM_TF = ROOT / "primary-aws/terraform/iam-node.tf"
VARIABLES_TF = ROOT / "primary-aws/terraform/variables-iam.tf"
OUTPUTS_TF = ROOT / "primary-aws/terraform/outputs-iam.tf"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/terraform-deploy.yml"
TEARDOWN_WORKFLOW = ROOT / ".github/workflows/terraform-teardown.yml"


PROVIDER_658_IAM_ACTIONS = {
    "iam:AddRoleToInstanceProfile",
    "iam:CreateInstanceProfile",
    "iam:CreateRole",
    "iam:DeleteInstanceProfile",
    "iam:DeleteRole",
    "iam:DeleteRolePolicy",
    "iam:GetInstanceProfile",
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListAttachedRolePolicies",
    "iam:ListInstanceProfilesForRole",
    "iam:ListRolePolicies",
    "iam:PassRole",
    "iam:PutRolePermissionsBoundary",
    "iam:PutRolePolicy",
    "iam:RemoveRoleFromInstanceProfile",
    "iam:TagInstanceProfile",
    "iam:TagRole",
    "iam:UntagInstanceProfile",
    "iam:UntagRole",
    "iam:UpdateAssumeRolePolicy",
    "iam:UpdateRole",
    "iam:UpdateRoleDescription",
}


PLAN_REFRESH_ACTIONS = {
    "cloudwatch:DescribeAlarms",
    "cloudwatch:ListTagsForResource",
    "ec2:Describe*",
    "iam:GetInstanceProfile",
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListAttachedRolePolicies",
    "iam:ListInstanceProfilesForRole",
    "iam:ListRolePolicies",
    "kms:DescribeKey",
    "kms:GetKeyPolicy",
    "kms:GetKeyRotationStatus",
    "kms:ListAliases",
    "kms:ListResourceTags",
    "s3:GetAccelerateConfiguration",
    "s3:GetBucketAcl",
    "s3:GetBucketCORS",
    "s3:GetBucketLocation",
    "s3:GetBucketLogging",
    "s3:GetBucketObjectLockConfiguration",
    "s3:GetBucketPolicy",
    "s3:GetBucketPublicAccessBlock",
    "s3:GetBucketRequestPayment",
    "s3:GetBucketTagging",
    "s3:GetBucketVersioning",
    "s3:GetBucketWebsite",
    "s3:GetEncryptionConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:GetReplicationConfiguration",
    "s3:ListBucket",
    "sns:GetSubscriptionAttributes",
    "sns:GetTopicAttributes",
    "sns:ListSubscriptionsByTopic",
    "sns:ListTagsForResource",
    "ssm:GetParameter",
    "ssm:ListTagsForResource",
    "sts:GetCallerIdentity",
}


APPLY_WORKLOAD_ACTIONS = {
    "cloudwatch:DeleteAlarms",
    "cloudwatch:DescribeAlarms",
    "cloudwatch:ListTagsForResource",
    "cloudwatch:PutMetricAlarm",
    "cloudwatch:TagResource",
    "cloudwatch:UntagResource",
    "kms:CreateAlias",
    "kms:CreateKey",
    "kms:DeleteAlias",
    "kms:DescribeKey",
    "kms:DisableKeyRotation",
    "kms:EnableKeyRotation",
    "kms:GetKeyPolicy",
    "kms:GetKeyRotationStatus",
    "kms:ListAliases",
    "kms:ListResourceTags",
    "kms:PutKeyPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:TagResource",
    "kms:UpdateAlias",
    "kms:UpdateKeyDescription",
    "s3:CreateBucket",
    "s3:DeleteBucketEncryption",
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:DeleteBucket",
    "s3:DeletePublicAccessBlock",
    "s3:GetBucketPublicAccessBlock",
    "s3:GetBucketTagging",
    "s3:GetBucketVersioning",
    "s3:GetEncryptionConfiguration",
    "s3:ListBucket",
    "s3:ListBucketVersions",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutBucketTagging",
    "s3:PutBucketVersioning",
    "s3:PutEncryptionConfiguration",
    "s3:TagResource",
    "s3:UntagResource",
    "sns:CreateTopic",
    "sns:DeleteTopic",
    "sns:GetSubscriptionAttributes",
    "sns:GetTopicAttributes",
    "sns:ListSubscriptionsByTopic",
    "sns:ListTagsForResource",
    "sns:SetSubscriptionAttributes",
    "sns:SetTopicAttributes",
    "sns:Subscribe",
    "sns:TagResource",
    "sns:Unsubscribe",
    "sns:UntagResource",
}


def actions(policy: dict) -> set[str]:
    values: set[str] = set()
    for statement in policy["Statement"]:
        action = statement.get("Action", [])
        values.update([action] if isinstance(action, str) else action)
    return values


def actions_with_effect(policy: dict, effect: str) -> set[str]:
    values: set[str] = set()
    for item in policy["Statement"]:
        if item.get("Effect") != effect:
            continue
        action = item.get("Action", [])
        values.update([action] if isinstance(action, str) else action)
    return values


def apply_policy_from_template() -> dict:
    document = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    policies = document["Resources"]["TerraformApplyRole"]["Properties"]["Policies"]
    assert len(policies) == 1
    return policies[0]["PolicyDocument"]


def plan_policy_from_template() -> dict:
    document = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    policies = document["Resources"]["TerraformPlanRole"]["Properties"]["Policies"]
    assert len(policies) == 1
    return policies[0]["PolicyDocument"]


def statement(policy: dict, sid: str) -> dict:
    return next(item for item in policy["Statement"] if item.get("Sid") == sid)


def policy_text_failures(text: str) -> list[str]:
    failures: list[str] = []
    required = {
        '"iam:PassedToService": "ec2.amazonaws.com"': "PassRole EC2 service condition",
        '"iam:PermissionsBoundary"': "permissions-boundary condition",
        '"iam:CreateRole"': "CreateRole",
        '"iam:PutRolePolicy"': "PutRolePolicy",
        '"iam:PutRolePermissionsBoundary"': "PutRolePermissionsBoundary",
        '"iam:ListInstanceProfilesForRole"': "destroy lifecycle action",
        '"iam:TagInstanceProfile"': "instance profile tag lifecycle action",
        '"iam:UntagInstanceProfile"': "instance profile untag lifecycle action",
        '"iam:UntagRole"': "role untag lifecycle action",
        '"iam:UpdateAssumeRolePolicy"': "trust update lifecycle action",
        '"iam:UpdateRole"': "role update lifecycle action",
        '"Effect": "Deny"': "explicit CI self-management deny",
        '"iam:CreateOpenIDConnectProvider"': "OIDC self-management deny",
    }
    for needle, name in required.items():
        if needle not in text:
            failures.append(name)
    if '"iam:AttachRolePolicy"' in text or '"iam:DetachRolePolicy"' in text:
        failures.append("obsolete managed-policy attachment permission")
    for statement in json.loads(text)["Statement"]:
        if statement["Effect"] == "Allow":
            action = statement.get("Action", [])
            statement_actions = {action} if isinstance(action, str) else set(action)
            if "iam:UpdateRole" in statement_actions:
                resource = json.dumps(statement.get("Resource"))
                if resource == '"*"' or "TerraformPlanRole" in resource or "TerraformApplyRole" in resource:
                    failures.append("CI role self-management permission")
    return failures


class TerraformCicdIamContractTests(unittest.TestCase):
    def read_template(self) -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    def test_bootstrap_template_has_exact_oidc_subjects_and_no_subject_wildcard(self) -> None:
        template = self.read_template()
        document = json.loads(template)
        repository_name_parts = {
            "RepositoryOwner": {
                "Fn::Select": [0, {"Fn::Split": ["/", {"Ref": "GitHubRepository"}]}]
            },
            "RepositoryName": {
                "Fn::Select": [1, {"Fn::Split": ["/", {"Ref": "GitHubRepository"}]}]
            },
        }
        immutable_main_subject = {
            "Fn::Sub": [
                "repo:${RepositoryOwner}@${GitHubRepositoryOwnerId}/${RepositoryName}@${GitHubRepositoryId}:ref:refs/heads/main",
                repository_name_parts,
            ]
        }
        immutable_deploy_subject = {
            "Fn::Sub": [
                "repo:${RepositoryOwner}@${GitHubRepositoryOwnerId}/${RepositoryName}@${GitHubRepositoryId}:environment:${GitHubEnvironment}",
                repository_name_parts,
            ]
        }
        immutable_teardown_subject = {
            "Fn::Sub": [
                "repo:${RepositoryOwner}@${GitHubRepositoryOwnerId}/${RepositoryName}@${GitHubRepositoryId}:environment:${GitHubTeardownEnvironment}",
                repository_name_parts,
            ]
        }
        self.assertIn("AWS::IAM::OIDCProvider", template)
        self.assertIn("token.actions.githubusercontent.com:aud", template)
        self.assertIn("sts.amazonaws.com", template)
        self.assertEqual(
            "^[0-9]+$", document["Parameters"]["GitHubRepositoryOwnerId"]["AllowedPattern"]
        )
        self.assertEqual(
            "^[0-9]+$", document["Parameters"]["GitHubRepositoryId"]["AllowedPattern"]
        )
        parameter_example = {
            item["ParameterKey"]: item["ParameterValue"]
            for item in json.loads(BOOTSTRAP_PARAMETERS.read_text(encoding="utf-8"))
        }
        self.assertRegex(parameter_example["GitHubRepositoryOwnerId"], r"^[0-9]+$")
        self.assertRegex(parameter_example["GitHubRepositoryId"], r"^[0-9]+$")
        plan_trust = document["Resources"]["TerraformPlanRole"]["Properties"]["AssumeRolePolicyDocument"]
        self.assertEqual(
            immutable_main_subject,
            plan_trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"],
        )
        self.assertNotIn("token.actions.githubusercontent.com:sub: '*'", template)
        self.assertEqual(
            ["hoodi-testnet-dev-teardown"],
            document["Parameters"]["GitHubTeardownEnvironment"]["AllowedValues"],
        )
        apply_trust = document["Resources"]["TerraformApplyRole"]["Properties"]["AssumeRolePolicyDocument"]
        self.assertEqual(
            [immutable_deploy_subject, immutable_teardown_subject],
            apply_trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"],
        )
        self.assertNotIn("repo:${GitHubRepository}:ref:refs/heads/main", template)
        self.assertNotIn("repo:${GitHubRepository}:environment:", template)
        self.assertEqual(
            {"Ref": "GitHubEnvironment"},
            document["Outputs"]["GitHubEnvironment"]["Value"],
        )
        self.assertEqual(
            {"Ref": "GitHubTeardownEnvironment"},
            document["Outputs"]["GitHubTeardownEnvironment"]["Value"],
        )

    def test_bootstrap_supports_existing_oidc_and_sequential_state_bucket_migration(self) -> None:
        template = self.read_template()
        for required in (
            "ExistingGithubOidcProviderArn",
            "CreateGithubOidcProvider",
            "StateBucketMode",
            "ExistingStateBucketName",
            "ExistingStateKmsKeyArn",
            "CreateStateBucket",
            "AWS::S3::Bucket",
            "StateBucketArn",
            "PlanArtifactBucketArn",
            "NodePermissionsBoundaryArn",
        ):
            self.assertIn(required, template)
        self.assertNotIn("AWS::CloudFormation::ResourceImport", template)

    def test_plan_artifact_bucket_has_protection_lifecycle_and_cloudtrail_readiness(self) -> None:
        document = json.loads(self.read_template())
        bucket = document["Resources"]["PlanArtifactBucket"]["Properties"]
        self.assertEqual(
            {"BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"},
            {key for key, value in bucket["PublicAccessBlockConfiguration"].items() if value},
        )
        self.assertEqual("Enabled", bucket["VersioningConfiguration"]["Status"])
        self.assertEqual(
            "aws:kms",
            bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"],
        )
        self.assertEqual(
            1,
            bucket["LifecycleConfiguration"]["Rules"][0]["ExpirationInDays"],
        )
        policy = document["Resources"]["PlanArtifactBucketPolicy"]["Properties"]["PolicyDocument"]
        policy_json = json.dumps(policy)
        self.assertNotIn("s3:CompleteMultipartUpload", actions(policy))
        self.assertIn("aws:SecureTransport", policy_json)
        self.assertIn("s3:x-amz-server-side-encryption", policy_json)
        self.assertIn("cloudtrail.amazonaws.com", policy_json)
        for logical_id in ("StateBucket", "StateBucketPolicy", "PlanArtifactBucket", "PlanArtifactBucketPolicy"):
            with self.subTest(logical_id=logical_id):
                self.assertEqual("Retain", document["Resources"][logical_id]["DeletionPolicy"])
                self.assertEqual("Retain", document["Resources"][logical_id]["UpdateReplacePolicy"])

    def test_apply_policy_has_provider_658_iam_lifecycle_without_managed_attachments(self) -> None:
        policy = apply_policy_from_template()
        reference_policy = json.loads(APPLY_POLICY.read_text(encoding="utf-8"))
        self.assertTrue(PROVIDER_658_IAM_ACTIONS <= actions_with_effect(policy, "Allow"))
        self.assertFalse(
            {"iam:AttachRolePolicy", "iam:DetachRolePolicy"} & actions(policy)
        )
        self.assertEqual([], policy_text_failures(json.dumps(policy)))
        self.assertEqual(actions(policy), actions(reference_policy))

    def test_apply_role_kms_lifecycle_is_tag_and_alias_scoped_in_both_policy_artifacts(self) -> None:
        canonical_policy = apply_policy_from_template()
        reference_policy = json.loads(APPLY_POLICY.read_text(encoding="utf-8"))

        required_tag_conditions = {
            "aws:ResourceTag/Project": "eth-failover",
            "aws:ResourceTag/Network": "hoodi",
            "aws:ResourceTag/Role": "primary",
            "aws:ResourceTag/Managed": "terraform",
        }
        request_tag_conditions = {
            "aws:RequestTag/Project": "eth-failover",
            "aws:RequestTag/Network": "hoodi",
            "aws:RequestTag/Role": "primary",
            "aws:RequestTag/Managed": "terraform",
            "aws:RequestTag/Purpose": [
                "primary-keystore-envelope",
                "recovery-keystore-envelope",
            ],
            "kms:KeySpec": "SYMMETRIC_DEFAULT",
            "kms:KeyUsage": "ENCRYPT_DECRYPT",
        }
        key_resource = {
            "canonical": {"Fn::Sub": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:key/*"},
            "reference": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:key/*",
        }
        caller_account = {
            "canonical": {"Ref": "AWS::AccountId"},
            "reference": "${AWS::AccountId}",
        }
        alias_resources = {
            "canonical": [
                {"Fn::Sub": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-staking-hoodi-keystore"},
                {"Fn::Sub": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-staking-hoodi-keystore-recovery"},
                {"Fn::Sub": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-validator-keys-hoodi"},
            ],
            "reference": [
                "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-staking-hoodi-keystore",
                "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-staking-hoodi-keystore-recovery",
                "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:alias/eth-validator-keys-hoodi",
            ],
        }
        for name, policy in (("canonical", canonical_policy), ("reference", reference_policy)):
            with self.subTest(policy=name):
                self.assertNotIn(
                    "ManageTerraformKmsResources",
                    {item["Sid"] for item in policy["Statement"]},
                )
                create = statement(policy, "CreateOnlyTaggedTerraformKmsKeys")
                self.assertEqual("kms:CreateKey", create["Action"])
                self.assertEqual("*", create["Resource"])
                self.assertEqual(
                    request_tag_conditions,
                    create["Condition"]["StringEquals"],
                )
                self.assertEqual(
                    {"kms:BypassPolicyLockoutSafetyCheck": "false"},
                    create["Condition"]["Bool"],
                )
                self.assertEqual(
                    ["Managed", "Network", "Project", "Purpose", "Role"],
                    create["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"],
                )

                tag_on_create = statement(policy, "TagOnlyNewTerraformKmsKeys")
                self.assertEqual("kms:TagResource", tag_on_create["Action"])
                self.assertEqual(key_resource[name], tag_on_create["Resource"])
                self.assertEqual(
                    {
                        **request_tag_conditions,
                        "kms:CallerAccount": caller_account[name],
                    },
                    tag_on_create["Condition"]["StringEquals"],
                )
                self.assertEqual(
                    ["Managed", "Network", "Project", "Purpose", "Role"],
                    tag_on_create["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"],
                )

                manage = statement(policy, "ManageOnlyTaggedTerraformKmsKeys")
                self.assertNotEqual("*", manage["Resource"])
                self.assertEqual(key_resource[name], manage["Resource"])
                self.assertEqual(
                    required_tag_conditions,
                    manage["Condition"]["StringEquals"],
                )
                self.assertFalse(
                    {"kms:TagResource", "kms:UntagResource"}
                    & set(manage["Action"]),
                    "routine lifecycle must not mutate the ABAC guard tags",
                )

                alias_targets = statement(policy, "UseOnlyTaggedTerraformKmsAliasTargets")
                self.assertEqual(
                    {"kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias"},
                    set(alias_targets["Action"]),
                )
                self.assertEqual(key_resource[name], alias_targets["Resource"])
                self.assertEqual(
                    required_tag_conditions,
                    alias_targets["Condition"]["StringEquals"],
                )

                aliases = statement(policy, "ManageOnlyExactTerraformKmsAliases")
                self.assertEqual(alias_resources[name], aliases["Resource"])
                self.assertEqual(
                    {"kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias"},
                    set(aliases["Action"]),
                )

                list_aliases = statement(policy, "ListKmsAliasesForProviderRefresh")
                self.assertEqual("kms:ListAliases", list_aliases["Action"])
                self.assertEqual("*", list_aliases["Resource"])

    def test_retained_break_glass_role_survives_identity_center_suffix_rotation(self) -> None:
        document = json.loads(self.read_template())
        role = document["Resources"]["KmsBreakGlassRole"]
        self.assertEqual("AWS::IAM::Role", role["Type"])
        self.assertEqual("Retain", role["DeletionPolicy"])
        self.assertEqual("Retain", role["UpdateReplacePolicy"])
        self.assertEqual(
            "hoodi-testnet-dev-KmsBreakGlassRole",
            role["Properties"]["RoleName"],
        )
        trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        self.assertEqual(1, len(trust))
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:root"},
            trust[0]["Principal"]["AWS"],
        )
        self.assertEqual("sts:AssumeRole", trust[0]["Action"])
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_terraform_cicd_bootstrap_admin_*"},
            trust[0]["Condition"]["ArnLike"]["aws:PrincipalArn"],
        )
        recovery = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"][0]
        self.assertEqual("RecoverOnlyTaggedTerraformKmsKeys", recovery["Sid"])
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:kms:*:${AWS::AccountId}:key/*"},
            recovery["Resource"],
        )
        self.assertEqual(
            {
                "aws:ResourceTag/Project": "eth-failover",
                "aws:ResourceTag/Network": "hoodi",
                "aws:ResourceTag/Role": "primary",
                "aws:ResourceTag/Managed": "terraform",
            },
            recovery["Condition"]["StringEquals"],
        )
        self.assertEqual(
            {"Fn::GetAtt": ["KmsBreakGlassRole", "Arn"]},
            document["Outputs"]["KmsBreakGlassRoleArn"]["Value"],
        )

    def test_standalone_apply_policy_matches_canonical_ci_deny_resource_scope(self) -> None:
        canonical_policy = apply_policy_from_template()
        reference_policy = json.loads(APPLY_POLICY.read_text(encoding="utf-8"))

        canonical_deny = statement(canonical_policy, "DenyCiSelfManagement")
        canonical_deny_json = json.dumps(canonical_deny["Resource"], sort_keys=True)
        for canonical_target in (
            "${TerraformPlanRoleName}",
            "${TerraformApplyRoleName}",
            "KmsBreakGlassRole",
            "ExistingGithubOidcProviderArn",
        ):
            self.assertIn(canonical_target, canonical_deny_json)
        self.assertEqual(
            [
                "${TerraformPlanRoleArn}",
                "${TerraformApplyRoleArn}",
                "${KmsBreakGlassRoleArn}",
                "${GithubOidcProviderArn}",
            ],
            statement(reference_policy, "DenyCiSelfManagement")["Resource"],
        )

        reference_by_sid = {item["Sid"]: item for item in reference_policy["Statement"]}
        for purge_sid in (
            "ListApprovedStagingObjectVersions",
            "DeleteApprovedStagingObjectVersions",
        ):
            self.assertIn(purge_sid, reference_by_sid)

        canonical_list = statement(canonical_policy, "ListApprovedStagingObjectVersions")
        reference_list = reference_by_sid["ListApprovedStagingObjectVersions"]
        self.assertEqual("s3:ListBucketVersions", canonical_list["Action"])
        self.assertEqual(canonical_list["Action"], reference_list["Action"])
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}"},
            canonical_list["Resource"],
        )
        self.assertEqual(
            "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}",
            reference_list["Resource"],
        )

        canonical_delete = statement(canonical_policy, "DeleteApprovedStagingObjectVersions")
        reference_delete = reference_by_sid["DeleteApprovedStagingObjectVersions"]
        self.assertEqual(
            {"s3:DeleteObject", "s3:DeleteObjectVersion"},
            set(canonical_delete["Action"]),
        )
        self.assertEqual(set(canonical_delete["Action"]), set(reference_delete["Action"]))
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}/*"},
            canonical_delete["Resource"],
        )
        self.assertEqual(
            "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}/*",
            reference_delete["Resource"],
        )

    def test_plan_role_reads_state_but_only_mutates_native_lock(self) -> None:
        policy = plan_policy_from_template()
        state = statement(policy, "ReadOnlyState")
        lock = statement(policy, "ManageOnlyStateLock")
        self.assertEqual({"s3:GetObject", "s3:GetObjectVersion"}, set([state["Action"]] if isinstance(state["Action"], str) else state["Action"]))
        self.assertNotIn(".tflock", json.dumps(state["Resource"]))
        self.assertEqual(
            {"s3:GetObject", "s3:PutObject", "s3:DeleteObject"},
            set([lock["Action"]] if isinstance(lock["Action"], str) else lock["Action"]),
        )
        self.assertIn(".tflock", json.dumps(lock["Resource"]))

    def test_plan_role_can_refresh_the_complete_post_apply_graph(self) -> None:
        policy = plan_policy_from_template()
        self.assertTrue(PLAN_REFRESH_ACTIONS <= actions(policy))
        staging = statement(policy, "ReadOnlyTerraformStagingBucket")
        self.assertIn("eth-failover-hoodi-staging-${AWS::AccountId}", json.dumps(staging))

    def test_apply_role_has_current_workload_resource_lifecycle(self) -> None:
        policy = apply_policy_from_template()
        self.assertTrue(APPLY_WORKLOAD_ACTIONS <= actions_with_effect(policy, "Allow"))
        self.assertNotIn("s3:DeleteBucketTagging", actions(policy))
        self.assertNotIn("s3:GetBucketAccelerateConfiguration", actions(policy))
        self.assertNotIn("iam:UpdateOpenIDConnectProviderUrl", actions(policy))
        self.assertNotEqual("*", statement(policy, "DenyCiSelfManagement")["Resource"])
        deny_oidc_create = statement(policy, "DenyAnyOidcProviderCreation")
        self.assertEqual("Deny", deny_oidc_create["Effect"])
        self.assertEqual("iam:CreateOpenIDConnectProvider", deny_oidc_create["Action"])
        self.assertEqual("*", deny_oidc_create["Resource"])
        apply_role_json = json.dumps(
            json.loads(self.read_template())["Resources"]["TerraformApplyRole"]["Properties"]
        )
        self.assertNotIn('["TerraformApplyRole", "Arn"]', apply_role_json)
        protected = statement(policy, "DenyChangesToCiRolesAndBootstrapDataStores")
        protected_actions = {protected["Action"]} if isinstance(protected["Action"], str) else set(protected["Action"])
        self.assertTrue({"kms:DisableKeyRotation", "kms:EnableKeyRotation"} <= protected_actions)
        self.assertFalse(
            {"s3:PutBucketEncryption", "s3:PutBucketLifecycleConfiguration"}
            & actions(policy)
        )

    def test_staging_bucket_teardown_can_delete_managed_subresources_on_the_bucket_arn(self) -> None:
        canonical_policy = apply_policy_from_template()
        reference_policy = json.loads(APPLY_POLICY.read_text(encoding="utf-8"))
        canonical_staging = statement(canonical_policy, "ManageTerraformStagingBucket")
        reference_staging = statement(reference_policy, "ManageTerraformStagingBucket")
        teardown_actions = {
            "s3:DeleteBucketEncryption",
            "s3:DeleteBucketPolicy",
            "s3:DeletePublicAccessBlock",
            "s3:PutBucketPolicy",
        }

        self.assertTrue(teardown_actions <= set(canonical_staging["Action"]))
        self.assertTrue(teardown_actions <= set(reference_staging["Action"]))
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}"},
            canonical_staging["Resource"],
        )
        self.assertEqual(
            "arn:${AWS::Partition}:s3:::eth-failover-hoodi-staging-${AWS::AccountId}",
            reference_staging["Resource"],
        )

    def test_boundary_updates_require_exact_boundary_and_deletion_is_denied(self) -> None:
        policy = apply_policy_from_template()
        put_boundary = statement(policy, "KeepExactNodePermissionsBoundary")
        self.assertEqual("iam:PutRolePermissionsBoundary", put_boundary["Action"])
        self.assertEqual(
            {"Ref": "NodePermissionsBoundary"},
            put_boundary["Condition"]["StringEquals"]["iam:PermissionsBoundary"],
        )
        deny_delete = statement(policy, "DenyNodeBoundaryRemoval")
        self.assertEqual("Deny", deny_delete["Effect"])
        self.assertIn("iam:DeleteRolePermissionsBoundary", deny_delete["Action"])

    def test_apply_role_passrole_is_limited_to_ec2_and_node_boundary(self) -> None:
        policy = apply_policy_from_template()
        pass_statements = [
            statement
            for statement in policy["Statement"]
            if "iam:PassRole" in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
        ]
        self.assertEqual(1, len(pass_statements))
        self.assertEqual(
            "ec2.amazonaws.com",
            pass_statements[0]["Condition"]["StringEquals"]["iam:PassedToService"],
        )
        self.assertIn("iam:PermissionsBoundary", json.dumps(policy))

    def test_workload_node_role_requires_bootstrap_boundary_input_and_exports_it(self) -> None:
        if not IAM_TF.is_file():
            self.skipTest("terraform iam slice (T3) not yet imported")
        iam_tf = IAM_TF.read_text(encoding="utf-8")
        variables_tf = VARIABLES_TF.read_text(encoding="utf-8")
        outputs_tf = OUTPUTS_TF.read_text(encoding="utf-8")
        self.assertIn("permissions_boundary = var.node_permissions_boundary_arn", iam_tf)
        self.assertIn('variable "node_permissions_boundary_arn"', variables_tf)
        self.assertIn('output "node_permissions_boundary_arn"', outputs_tf)

    def test_bootstrap_values_are_looked_up_into_the_tracked_runtime_manifest(self) -> None:
        readme = BOOTSTRAP_README.read_text(encoding="utf-8")
        self.assertIn("primary-aws/terraform/ci/runtime-inputs.json", readme)
        self.assertIn("REPLACE_WITH_NODE_PERMISSIONS_BOUNDARY_ARN", readme)
        self.assertIn("REPLACE_WITH_KMS_BREAK_GLASS_ROLE_ARN", readme)
        self.assertIn("hoodi-testnet-dev-node-permissions-boundary", readme)
        self.assertIn("hoodi-testnet-dev-KmsBreakGlassRole", readme)
        self.assertIn("aws iam list-policies", readme)
        self.assertIn("aws iam get-role", readme)
        self.assertIn(
            '"${EDITOR:-vi}" primary-aws/terraform/ci/runtime-inputs.json', readme
        )
        self.assertIn("render-terraform-ci-runtime.py", readme)
        self.assertIn("Environment variables/secrets 0개", readme)
        self.assertNotIn("TF_VAR_", readme)
        self.assertNotIn("CI_NODE_PERMISSIONS_BOUNDARY_ARN", readme)

    def test_mutations_are_rejected_by_the_iam_contract(self) -> None:
        policy = json.dumps(apply_policy_from_template(), indent=2)
        self_management_policy = json.loads(policy)
        statement(self_management_policy, "ReadCallerIdentity")["Action"] = [
            "sts:GetCallerIdentity",
            "iam:UpdateRole",
        ]
        mutations = {
            "missing_passrole_ec2_condition": policy.replace(
                '"iam:PassedToService": "ec2.amazonaws.com"', '"iam:RemovedPassedToService": "removed"'
            ),
            "missing_permissions_boundary": policy.replace('"iam:PermissionsBoundary"', '"iam:RemovedBoundary"'),
            "missing_destroy_lifecycle_action": policy.replace(
                '"iam:ListInstanceProfilesForRole",\n', ""
            ),
            "ci_self_management_added": json.dumps(self_management_policy),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                self.assertTrue(policy_text_failures(mutated))

    def test_boundary_policy_is_a_real_permissions_ceiling(self) -> None:
        document = json.loads(self.read_template())
        policy = document["Resources"]["NodePermissionsBoundary"]["Properties"]["PolicyDocument"]
        boundary_actions = actions(policy)
        self.assertEqual("2012-10-17", policy["Version"])
        self.assertTrue(any(statement["Effect"] == "Allow" for statement in policy["Statement"]))
        self.assertFalse(any(action.startswith("iam:") for action in boundary_actions))
        self.assertFalse(any(action.startswith("sts:AssumeRole") for action in boundary_actions))
        self.assertEqual(
            "EthFailover/hoodi",
            statement(policy, "PublishOnlyHoodiHeartbeat")["Condition"]["StringEquals"]["cloudwatch:namespace"],
        )
        self.assertIn("parameter/eth-staking/hoodi/*", json.dumps(statement(policy, "ReadOnlyHoodiParameters")))
        self.assertIn("eth-failover-hoodi-alerts", json.dumps(statement(policy, "PublishOnlyHoodiAlerts")))
        self.assertIn("eth-failover-hoodi-staging-${AWS::AccountId}/staged/*", json.dumps(statement(policy, "ReadOnlyHoodiStagingObjects")))
        kms = statement(policy, "DecryptOnlyPrimaryHoodiKeystoreKeys")
        self.assertIn("arn:${AWS::Partition}:kms:${AWS::Region}:${AWS::AccountId}:key/*", json.dumps(kms))
        self.assertEqual("eth-failover", kms["Condition"]["StringEquals"]["aws:ResourceTag/Project"])
        for item in policy["Statement"]:
            if item["Sid"] != "PublishOnlyHoodiHeartbeat":
                self.assertNotEqual("*", item["Resource"])

    def test_artifact_policy_covers_the_canonical_deploy_and_teardown_keys(self) -> None:
        template = self.read_template()
        self.assertIn("${PlanArtifactBucket.Arn}/${GitHubRepository}/*", template)
        self.assertIn(
            'plan_object_key="$GITHUB_REPOSITORY/hoodi-testnet-dev/',
            DEPLOY_WORKFLOW.read_text(encoding="utf-8"),
        )
        if TEARDOWN_WORKFLOW.is_file():
            self.assertIn(
                'plan_object_key="$GITHUB_REPOSITORY/hoodi-testnet-dev-teardown/',
                TEARDOWN_WORKFLOW.read_text(encoding="utf-8"),
            )

    def test_existing_state_mode_does_not_require_an_unused_create_bucket_name(self) -> None:
        document = json.loads(self.read_template())
        parameters = document["Parameters"]
        self.assertEqual("", parameters["StateBucketName"]["Default"])
        assertions = document["Rules"]["CreateStateBucketRequiresName"]["Assertions"]
        self.assertTrue(any("StateBucketName" in json.dumps(item) for item in assertions))
        existing_assertions = document["Rules"]["ExistingStateBucketRequiresInputs"]["Assertions"]
        self.assertTrue(
            any(
                item["Assert"] == {"Fn::Equals": [{"Ref": "StateBucketName"}, ""]}
                for item in existing_assertions
            )
        )

    def test_bootstrap_imports_exact_ed25519_node_key_pair_without_private_material(self) -> None:
        document = json.loads(self.read_template())
        name = document["Parameters"]["NodeSshKeyPairName"]
        public_key = document["Parameters"]["NodeSshPublicKey"]
        self.assertEqual("eth-failover-hoodi", name["Default"])
        self.assertEqual(["eth-failover-hoodi"], name["AllowedValues"])
        self.assertTrue(public_key["AllowedPattern"].startswith("^ssh-ed25519 "))

        resource = document["Resources"]["NodeSshKeyPair"]
        self.assertEqual("AWS::EC2::KeyPair", resource["Type"])
        properties = resource["Properties"]
        self.assertEqual({"Ref": "NodeSshKeyPairName"}, properties["KeyName"])
        self.assertEqual({"Ref": "NodeSshPublicKey"}, properties["PublicKeyMaterial"])
        self.assertEqual("ed25519", properties["KeyType"])
        self.assertEqual(
            {"Project": "eth-failover", "Network": "hoodi", "Managed": "cloudformation"},
            {item["Key"]: item["Value"] for item in properties["Tags"]},
        )
        self.assertNotIn("PrivateKey", json.dumps(document))
        self.assertEqual({"Ref": "NodeSshKeyPair"}, document["Outputs"]["NodeSshKeyPairName"]["Value"])
        self.assertEqual(
            {"Fn::GetAtt": ["NodeSshKeyPair", "KeyFingerprint"]},
            document["Outputs"]["NodeSshKeyFingerprint"]["Value"],
        )
        apply_policy = apply_policy_from_template()
        deny = statement(apply_policy, "DenyBootstrapNodeSshKeyPairMutation")
        self.assertEqual("Deny", deny["Effect"])
        self.assertTrue(
            {"ec2:CreateKeyPair", "ec2:ImportKeyPair", "ec2:DeleteKeyPair", "ec2:CreateTags", "ec2:DeleteTags"}
            <= set(deny["Action"])
        )
        self.assertEqual(
            {"Fn::Sub": "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:key-pair/${NodeSshKeyPairName}"},
            deny["Resource"],
        )


if __name__ == "__main__":
    unittest.main()
