resource "aws_ebs_volume" "chain" {
  availability_zone = aws_subnet.public.availability_zone
  encrypted         = true
  type              = "gp3"
  size              = var.chain_volume_size_gib
  iops              = 6000
  throughput        = 500

  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = "${local.name}-chain", DataClass = "rebuildable-chain" }
}

resource "aws_ebs_volume" "validator" {
  availability_zone = aws_subnet.public.availability_zone
  encrypted         = true
  type              = "gp3"
  size              = var.validator_volume_size_gib

  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = "${local.name}-validator", DataClass = "slashing-protection" }
}

resource "aws_volume_attachment" "chain" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.chain.id
  instance_id = aws_instance.standby.id
}

resource "aws_volume_attachment" "validator" {
  device_name = "/dev/sdg"
  volume_id   = aws_ebs_volume.validator.id
  instance_id = aws_instance.standby.id
}
