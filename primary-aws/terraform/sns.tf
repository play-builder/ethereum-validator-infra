# 알림 경로 — 영속 인프라이므로 Terraform이 소유한다 (P01은 "주소 확정"까지만).
#
# email 프로토콜 주의(공식 caveat):
#   - 구독 생성은 IaC가 하지만, 활성화는 수신자가 확인 링크를 눌러야 한다(사람의 몫).
#   - 미확인(pending confirmation) 구독은 AWS가 unsubscribe를 허용하지 않아
#     terraform destroy 시 state에서만 사라지고 AWS에 잔존할 수 있다.
#   → 운영 규율: apply 직후 2인 모두 즉시 확인 클릭을 완료한다 (part-02 절차).

resource "aws_sns_topic" "alerts" {
  name = "${local.project}-${var.network}-alerts"
}

resource "aws_sns_topic_subscription" "operators" {
  for_each  = toset(var.operator_alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# Alertmanager(sns_configs)가 경보를 게시할 최소 권한 — 대상 토픽 하나로 한정
resource "aws_iam_role_policy" "sns_publish_alerts" {
  name = "sns-publish-alerts"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sns:Publish"]
      Resource = aws_sns_topic.alerts.arn
    }]
  })
}
