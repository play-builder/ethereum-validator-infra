variable "node_permissions_boundary_arn" {
  description = "CI bootstrap stack가 먼저 생성한 node role 전용 IAM permissions boundary ARN. Terraform-managed node role에는 항상 이 경계를 강제한다."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:policy/[A-Za-z0-9+=,.@_/-]+$", var.node_permissions_boundary_arn))
    error_message = "node_permissions_boundary_arn must be an IAM managed-policy ARN from the CI/CD bootstrap stack."
  }
}

variable "param_prefix_override" {
  description = "SSM 파라미터 prefix 강제 지정(빈 값이면 /eth-staking/<network>). R3: IAM과 SSM이 같은 local에서 파생된다."
  type        = string
  default     = ""
}
