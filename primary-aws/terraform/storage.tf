# R4 수정: validator 상태(슬래싱 보호 DB 포함) 전용 EBS를 "정식 리소스"로.
#  - chaindata와 분리 → EL/CL 재구축·리사이즈가 SP DB를 건드리지 않는다
#  - EBS는 다중 attach 불가(gp3) → "SP DB 디스크는 물리적으로 한 인스턴스에만" 펜스
#  - 인스턴스 사망 시(시나리오 A) 이 볼륨만 복구 인스턴스에 붙여 fresh export (RB-01 F3-①b)

resource "aws_ebs_volume" "chaindata" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.chaindata_volume_gb
  type              = "gp3"
  iops              = var.chaindata_iops
  throughput        = var.chaindata_throughput_mbps
  encrypted         = true
  tags = {
    Name = "${local.project}-${var.network}-chaindata"
    Role = "chaindata"
  }
}

resource "aws_ebs_volume" "validator_state" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.validator_state_volume_gb
  type              = "gp3"
  encrypted         = true
  tags = {
    Name = "${local.project}-${var.network}-validator-state"
    Role = "slashing-protection"
  }
}
