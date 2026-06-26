data "aws_ssm_parameter" "ubuntu_ami" {
  count = var.ami_id == "" ? 1 : 0
  name  = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "node" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ssm_parameter.ubuntu_ami[0].value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.node.id]
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.node.name
  ebs_optimized          = true
  # 실수 방어. F1의 stop은 허용, terminate는 2단계 teardown 승인이 있어야 가능.
  # 승인된 준비 plan(allow_protected_destroy=true)만 이 보호를 해제하고 state에 기록한다.
  disable_api_termination = var.allow_protected_destroy ? false : true
  monitoring              = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 강제
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = file("${path.module}/user-data.sh")

  tags = {
    Name = "${local.project}-${var.network}-primary"
  }

  lifecycle {
    # 밸리데이터 호스트에 create_before_destroy는 금기(계획서 §1 — 이중 실행 제조기).
    ignore_changes = [ami] # AMI 갱신은 계획된 재구축 change record로만
  }
}
