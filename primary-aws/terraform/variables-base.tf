variable "network" {
  description = "대상 네트워크. 본 저장소는 hoodi 전용이며 mainnet은 part-12 승인 단계 이후 별도 change request."
  type        = string
  default     = "hoodi"
  validation {
    condition     = contains(["hoodi", "mainnet"], var.network)
    error_message = "network must be hoodi or mainnet."
  }
}

variable "region" {
  description = "주노드 리전 (계획서: Seoul)"
  type        = string
  default     = "ap-northeast-2"
}

variable "kms_recovery_region" {
  description = "A2 이중 봉투 wrap-A용 KMS 리전. 반드시 var.region과 달라야 한다."
  type        = string
  default     = "eu-central-1"
  validation {
    condition     = var.kms_recovery_region != var.region
    error_message = "recovery KMS region must differ from the primary region (A2)."
  }
}

# ── 파이프라인 계약 변수 ──────────────────────────────────────────────────────
# deploy/teardown workflow가 렌더하는 tfvars에 항상 포함되므로, 슬라이스가 다
# 도착하기 전에도 경고가 없도록 T1(기반)에서 미리 선언한다. 실제 소비처는
# ec2.tf(T5)와 s3.tf(S1)다.

variable "allow_protected_destroy" {
  description = "보호된 EC2와 versioned staging bucket을 삭제할 수 있도록 provider state에 명시적으로 기록하는 2단계 teardown opt-in. 평상시는 false이며 별도 approved preparation plan에서만 true로 전환한다."
  type        = bool
  default     = false
}

variable "enable_staging_bucket" {
  description = "R8: 암호문 스테이징 전달용 S3(선택). 키 전달은 오프라인 경로 병행이 원칙."
  type        = bool
  default     = true
}
