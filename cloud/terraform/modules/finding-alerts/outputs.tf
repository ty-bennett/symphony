output "topic_arn" {
  description = "SNS topic receiving high-severity findings."
  value       = aws_sns_topic.alerts.arn
}
