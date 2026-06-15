locals {
  # SSM prefix의 기준이 되는 한 곳 — IAM 정책과 aws_ssm_parameter가 전부 여기서 파생된다.
  param_prefix = var.param_prefix_override != "" ? var.param_prefix_override : "/eth-staking/${var.network}"
}
