resource "aws_iam_role" "node" {
  name                 = "${local.project}-${var.network}-node"
  permissions_boundary = var.node_permissions_boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "node" {
  name = "${local.project}-${var.network}-node"
  role = aws_iam_role.node.name
}

# R3: SSM 허용 경로가 ssm.tf와 같은 local에서 파생 — "/eth/..." vs "/eth-staking/..." 류 불일치 원천 차단
resource "aws_iam_role_policy" "ssm_params" {
  name = "ssm-params"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
      Resource = "arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.param_prefix}/*"
    }]
  })
}
