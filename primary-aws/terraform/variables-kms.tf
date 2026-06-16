variable "terraform_plan_role_arn" {
  description = "CloudFormation CI bootstrap output의 exact TerraformPlanRole ARN. KMS refresh 조회만 허용하며 node boundary와 같은 account여야 한다."
  type        = string
  nullable    = false

  validation {
    condition = (
      can(regex("^arn:[^:]+:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.terraform_plan_role_arn)) &&
      try(
        split(":", var.terraform_plan_role_arn)[1] == split(":", var.node_permissions_boundary_arn)[1] &&
        split(":", var.terraform_plan_role_arn)[4] == split(":", var.node_permissions_boundary_arn)[4],
        false,
      )
    )
    error_message = "terraform_plan_role_arn must be an IAM role ARN in the same partition and account as node_permissions_boundary_arn."
  }
}

variable "terraform_apply_role_arn" {
  description = "CloudFormation CI bootstrap output의 exact TerraformApplyRole ARN. KMS key lifecycle administration principal은 이 role 하나로 고정하며 node boundary와 같은 account여야 한다."
  type        = string
  nullable    = false

  validation {
    condition = (
      can(regex("^arn:[^:]+:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.terraform_apply_role_arn)) &&
      try(
        split(":", var.terraform_apply_role_arn)[1] == split(":", var.node_permissions_boundary_arn)[1] &&
        split(":", var.terraform_apply_role_arn)[4] == split(":", var.node_permissions_boundary_arn)[4] &&
        var.terraform_apply_role_arn != var.terraform_plan_role_arn,
        false,
      )
    )
    error_message = "terraform_apply_role_arn must be a distinct IAM role ARN in the same partition and account as terraform_plan_role_arn and node_permissions_boundary_arn."
  }
}

variable "kms_break_glass_role_arn" {
  description = "CloudFormation CI bootstrap output의 retained KmsBreakGlassRole ARN. routine CI role이 재생성되어 KMS principal ID 연결이 끊겨도 이 고정 ARN으로 key policy를 복구하며 다른 CI role과 분리한다."
  type        = string
  nullable    = false

  validation {
    condition = (
      can(regex("^arn:[^:]+:iam::[0-9]{12}:role/hoodi-testnet-dev-KmsBreakGlassRole$", var.kms_break_glass_role_arn)) &&
      try(
        split(":", var.kms_break_glass_role_arn)[1] == split(":", var.node_permissions_boundary_arn)[1] &&
        split(":", var.kms_break_glass_role_arn)[4] == split(":", var.node_permissions_boundary_arn)[4] &&
        var.kms_break_glass_role_arn != var.terraform_plan_role_arn &&
        var.kms_break_glass_role_arn != var.terraform_apply_role_arn,
        false,
      )
    )
    error_message = "kms_break_glass_role_arn must be the distinct hoodi-testnet-dev-KmsBreakGlassRole ARN in the same partition and account as the CI roles and node boundary."
  }
}

variable "sso_operator_permission_sets" {
  description = "Recovery-key 암호화와 복호를 분리하는 IAM Identity Center Permission Set 이름. 첫째는 Encrypt 전용 Operator 1, 둘째는 Decrypt 전용 Operator 2이며 두 값은 서로 달라야 한다."
  type        = list(string)

  validation {
    condition = length(var.sso_operator_permission_sets) == 2 && length(toset(var.sso_operator_permission_sets)) == 2 && alltrue([
      for name in var.sso_operator_permission_sets :
      can(regex("^[A-Za-z0-9_+=,.@-]{1,32}$", name))
    ])
    error_message = "sso_operator_permission_sets must contain exactly two distinct permission set names."
  }
}
