variable "operator_alert_emails" {
  description = "SNS email 구독자(운영자 2인). apply 후 각자 확인 링크 클릭 필수 — 미확인 구독은 TF가 삭제 불가(공식 caveat)."
  type        = list(string)
  default     = []
  validation {
    condition     = length(var.operator_alert_emails) != 1
    error_message = "알림 수신자는 0명(미구성) 또는 2명 이상 — 1인 단일점 금지."
  }
}

variable "enable_deadman_alarm" {
  description = "P11에서 true로 전환. node 가동 전 활성화하면 즉시 울린다."
  type        = bool
  default     = false
}

variable "host_id" {
  description = "하트비트 지표의 Host 차원 (ansible host_id와 일치)."
  type        = string
  default     = "aws-primary-01"
}
