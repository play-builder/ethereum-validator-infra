resource "aws_key_pair" "standby" {
  key_name   = var.key_pair_name
  public_key = var.node_ssh_public_key
}

resource "aws_instance" "standby" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.node.id]
  key_name                    = aws_key_pair.standby.key_name
  iam_instance_profile        = data.aws_iam_instance_profile.node.name
  associate_public_ip_address = false
  disable_api_termination     = !var.allow_protected_destroy
  monitoring                  = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = 64
    delete_on_termination = true
  }

  tags = { Name = "${local.name}-01" }
}

resource "aws_eip" "standby" {
  domain   = "vpc"
  instance = aws_instance.standby.id
  tags     = { Name = "${local.name}-eip" }
}
