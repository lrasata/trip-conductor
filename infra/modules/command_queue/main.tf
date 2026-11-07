# One SQS queue per service for the saga's commands, with a dead-letter queue and alarms.
# The saga's role is allowed to send here, so the queue needs no policy of its own.

locals {
  queue_name = "${var.name_prefix}-${var.service_name}-commands"
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum, to leave time to investigate
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "this" {
  name                       = local.queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  # A command nobody picked up for an hour belongs to a saga that gave up long ago.
  message_retention_seconds = 3600
  receive_wait_time_seconds = 20 # long polling: fewer empty receives, lower cost
  sqs_managed_sse_enabled   = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only the DLQ's own source queue may redrive into it.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this.arn]
  })
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.queue_name}-dlq-not-empty"
  alarm_description   = "Commands for ${var.service_name} failed ${var.max_receive_count} times and were moved to the dead-letter queue."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
}

# Commands piling up without failing (e.g. the consumer is throttled or its trigger is disabled).
resource "aws_cloudwatch_metric_alarm" "oldest_message_age" {
  alarm_name          = "${local.queue_name}-backlog"
  alarm_description   = "The oldest command in the ${var.service_name} queue has waited more than ${var.max_message_age_seconds} seconds."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.this.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.max_message_age_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
}
