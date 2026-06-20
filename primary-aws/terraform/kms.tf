# TerraformApplyRole is the only routine principal that can mutate the
# Terraform-managed KMS key lifecycle or policy. A retained, dormant break-glass
# role uses a wildcard Principal plus exact aws:PrincipalArn condition so IAM
# role deletion/recreation does not destroy the recovery route. Human IAM
# Identity Center roles receive no KMS administration: Operator 1 can seal with
# Encrypt and upload only encrypted staging objects, while Operator 2 can unseal
# with Decrypt. Their IAM policies add same-account and resource-tag ABAC; key
# policies add exact role/account/service/context gates. AWS KMS supports
# aws:ResourceTag only in IAM policies, not key policies.

locals {
  kms_account_root_principal = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
  kms_administration_actions = [
    "kms:CancelKeyDeletion",
    "kms:DescribeKey",
    "kms:DisableKey",
    "kms:DisableKeyRotation",
    "kms:EnableKey",
    "kms:EnableKeyRotation",
    "kms:GetKeyPolicy",
    "kms:GetKeyRotationStatus",
    "kms:PutKeyPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:TagResource",
    "kms:UntagResource",
    "kms:UpdateKeyDescription",
  ]
}

data "aws_iam_policy_document" "keystore" {
  statement {
    sid       = "AllowTerraformApplyRoleAdministration"
    effect    = "Allow"
    actions   = local.kms_administration_actions
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [var.terraform_apply_role_arn]
    }
  }

  statement {
    sid       = "AllowStableKmsBreakGlassRoleAdministration"
    effect    = "Allow"
    actions   = local.kms_administration_actions
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [var.kms_break_glass_role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "AllowTerraformPlanRoleReadOnly"
    effect    = "Allow"
    actions   = ["kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [var.terraform_plan_role_arn]
    }
  }

  statement {
    sid       = "AllowHumanOperatorsDescribePrimaryKey"
    effect    = "Allow"
    actions   = ["kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.kms_account_root_principal]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values = [
        local.kms_seal_operator_role_arn_pattern,
        local.kms_unseal_operator_role_arn_pattern,
      ]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "AllowOperator1StagingEnvelopeViaS3"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.kms_account_root_principal]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = [local.kms_seal_operator_role_arn_pattern]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values   = ["arn:${data.aws_partition.current.partition}:s3:::${local.project}-${var.network}-staging-${data.aws_caller_identity.current.account_id}"]
    }
  }

  statement {
    sid       = "AllowPrimaryNodeDecrypt"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.node.arn]
    }
  }
}

data "aws_iam_policy_document" "recovery" {
  statement {
    sid       = "AllowTerraformApplyRoleAdministration"
    effect    = "Allow"
    actions   = local.kms_administration_actions
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [var.terraform_apply_role_arn]
    }
  }

  statement {
    sid       = "AllowStableKmsBreakGlassRoleAdministration"
    effect    = "Allow"
    actions   = local.kms_administration_actions
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [var.kms_break_glass_role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "AllowTerraformPlanRoleReadOnly"
    effect    = "Allow"
    actions   = ["kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [var.terraform_plan_role_arn]
    }
  }

  statement {
    sid       = "AllowOperator1SealRecoveryKey"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.kms_account_root_principal]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = [local.kms_seal_operator_role_arn_pattern]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "AllowOperator2UnsealRecoveryKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.kms_account_root_principal]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = [local.kms_unseal_operator_role_arn_pattern]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "keystore" {
  description             = "${local.project}/${var.network}: keystore staging envelope (primary region)"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.keystore.json
  tags = merge(local.common_tags, {
    Purpose = "primary-keystore-envelope"
  })
}

resource "aws_kms_alias" "keystore_primary" {
  name          = local.kms_alias_primary
  target_key_id = aws_kms_key.keystore.key_id
}

resource "aws_kms_alias" "keystore_docs_compat" {
  name          = local.kms_alias_docs
  target_key_id = aws_kms_key.keystore.key_id
}

# 주노드 인스턴스는 primary-region keystore 키의 Decrypt만 (recovery 키는 절대 아님 —
# recovery 복호는 운영자 principal의 MFA 세션에서만 일어난다. RB-01 F4)
resource "aws_iam_role_policy" "kms_keystore_decrypt" {
  name = "kms-keystore-decrypt"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Decrypt"]
      Resource = aws_kms_key.keystore.arn
    }]
  })
}
