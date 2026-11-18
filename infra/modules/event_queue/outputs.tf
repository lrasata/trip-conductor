output "queue_arn" {
  value = aws_sqs_queue.this.arn
}

output "queue_url" {
  value = aws_sqs_queue.this.id
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}

output "rule_arn" {
  description = "A replay can be limited to this rule, so it reaches this service only."
  value       = aws_cloudwatch_event_rule.this.arn
}
