# R6: 소비자는 `terraform output -json` 한 번만 호출한다(다중 인자 output 금지).
output "node_permissions_boundary_arn" {
  description = "Terraform-managed node role에 강제된 bootstrap permissions boundary ARN"
  value       = aws_iam_role.node.permissions_boundary
}
output "param_prefix" {
  value = local.param_prefix
}
