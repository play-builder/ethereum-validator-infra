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
