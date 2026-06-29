output "sns_topic_arn" {
  description = "P02에서 lab.env [기록]에 영속화, P11 알람이 소비"
  value       = aws_sns_topic.alerts.arn
}
