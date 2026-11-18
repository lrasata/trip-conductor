# A service's subscription to the trip-events bus: an EventBridge rule, the SQS queue it
# delivers to, a dead-letter queue and alarms. Events that EventBridge itself can't deliver
# go to the same DLQ, so nothing is dropped silently.

locals {
  queue_name = "${var.name_prefix}-${var.service_name}-events"
}

resource "aws_cloudwatch_event_rule" "this" {
  name           = "${var.name_prefix}-${var.service_name}"
  description    = var.description
  event_bus_name = var.event_bus_name
  event_pattern  = jsonencode(var.event_pattern)
}

resource "aws_cloudwatch_event_target" "this" {
  rule           = aws_cloudwatch_event_rule.this.name
  event_bus_name = var.event_bus_name
  target_id      = "queue"
  arn            = aws_sqs_queue.this.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 10
  }

  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum, to leave time to investigate
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "this" {
  name                       = local.queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = 345600 # 4 days: an event must survive a consumer outage
  receive_wait_time_seconds  = 20     # long polling: fewer empty receives, lower cost
  sqs_managed_sse_enabled    = true

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

# Only this rule may send to the queue, and to the DLQ (events it couldn't deliver).
data "aws_iam_policy_document" "from_rule" {
  for_each = {
    queue = aws_sqs_queue.this.arn
    dlq   = aws_sqs_queue.dlq.arn
  }

  statement {
    actions   = ["sqs:SendMessage"]
    resources = [each.value]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.this.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "queue" {
  queue_url = aws_sqs_queue.this.id
  policy    = data.aws_iam_policy_document.from_rule["queue"].json
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.from_rule["dlq"].json
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.queue_name}-dlq-not-empty"
  alarm_description   = "Events for ${var.service_name} failed ${var.max_receive_count} times, or couldn't be delivered, and were moved to the dead-letter queue."
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

# Events piling up without failing (e.g. the consumer is throttled or its trigger is disabled).
resource "aws_cloudwatch_metric_alarm" "oldest_message_age" {
  alarm_name          = "${local.queue_name}-backlog"
  alarm_description   = "The oldest event in the ${var.service_name} queue has waited more than ${var.max_message_age_seconds} seconds."
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
