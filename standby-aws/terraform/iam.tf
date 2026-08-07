# IAM is account-global. Reuse the bounded node profile created in CH01
# instead of widening the ApplyRole to create a second IAM role.
data "aws_iam_instance_profile" "node" {
  name = var.node_instance_profile_name
}
