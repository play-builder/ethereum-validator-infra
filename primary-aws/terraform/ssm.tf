# R3 수정의 핵심: 모든 파라미터 이름이 local.param_prefix에서 파생된다.
# IAM(iam.tf)의 허용 리소스도 같은 local을 쓴다 — 경로가 어긋날 방법이 없다.

resource "aws_ssm_parameter" "checkpoint_sync_url" {
  name        = "${local.param_prefix}/checkpoint_sync_url"
  description = "Lighthouse BN --checkpoint-sync-url. [실습 직전 확인] eth-clients/checkpoint-sync-endpoints"
  type        = "String"
  value       = "https://hoodi.checkpoint.sigp.io"
}

resource "aws_ssm_parameter" "graffiti" {
  name        = "${local.param_prefix}/graffiti"
  description = "D8: 서명 주체 각인. primary=p-aws"
  type        = "String"
  value       = "p-aws"
}

resource "aws_ssm_parameter" "fee_recipient" {
  name        = "${local.param_prefix}/fee_recipient"
  description = "제안 보상 수령 주소. 운영자가 실값으로 갱신(placeholder는 기동 전 검사에서 거부)."
  type        = "String"
  value       = "0x0000000000000000000000000000000000000000"
  lifecycle { ignore_changes = [value] } # 운영자가 콘솔/CLI로 갱신한 값을 TF가 되돌리지 않는다
}
