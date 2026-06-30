# dead-man 알람 — 하트비트 지표의 "부재"가 경보다 (part-11에서 var로 활성화).
# 알람의 권한은 SNS 통지뿐이며 어떤 자동 전환도 없다(D1).
# node가 뜨기 전에 만들면 즉시 울리므로 기본 비활성 → P11에서 enable_deadman_alarm=true.

resource "aws_cloudwatch_metric_alarm" "heartbeat_missing" {
  count               = var.enable_deadman_alarm ? 1 : 0
  alarm_name          = "${local.project}-${var.network}-heartbeat-missing"
  alarm_description   = "node_heartbeat 부재 = 주노드 침묵. 조치는 사람(RB-01 F0부터). D1: 자동 전환 없음"
  namespace           = "EthFailover/${var.network}"
  metric_name         = "node_heartbeat"
  dimensions          = { Host = var.host_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
