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
