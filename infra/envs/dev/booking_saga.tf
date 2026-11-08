# The booking saga: the Step Functions state machine in statemachine/.

locals {
  booking_saga_service = "${local.name_prefix}-booking-saga"
  booking_saga_count   = local.deploy["booking-saga"] ? 1 : 0
  # Built by hand: the services' roles need it even before the saga is deployed.
  booking_saga_arn = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.booking_saga_service}"
}

# Errors only, without execution data: inputs and task tokens must not reach the logs.
resource "aws_cloudwatch_log_group" "booking_saga" {
  count = local.booking_saga_count

  name              = "/aws/vendedlogs/states/${local.booking_saga_service}"
  retention_in_days = 14
}

resource "aws_sfn_state_machine" "booking_saga" {
  count = local.booking_saga_count

  name     = local.booking_saga_service
  role_arn = aws_iam_role.booking_saga[0].arn
  type     = "STANDARD"

  definition = templatefile("${path.root}/../../../statemachine/booking.asl.json", {
    event_bus_name   = aws_cloudwatch_event_bus.trip_events.name
    flight_queue_url = module.flight_commands[0].queue_url
    hotel_queue_url  = module.hotel_commands[0].queue_url
  })

  logging_configuration {
    level                  = "ERROR"
    include_execution_data = false
    log_destination        = "${aws_cloudwatch_log_group.booking_saga[0].arn}:*"
  }

  tracing_configuration {
    enabled = true
  }
}

# ---------------------------------------------------------------- alarms

# A cancelled trip ends the execution normally, so these only fire on real trouble: a
# compensation that kept failing, or a saga that hit its own timeout.
resource "aws_cloudwatch_metric_alarm" "booking_saga" {
  for_each = local.booking_saga_count == 1 ? {
    failed    = "ExecutionsFailed"
    timed-out = "ExecutionsTimedOut"
  } : {}

  alarm_name          = "${local.booking_saga_service}-${each.key}"
  alarm_description   = "A booking saga execution ended with ${each.value}. Open it in the Step Functions console; its holds expire on their own."
  namespace           = "AWS/States"
  metric_name         = each.value
  dimensions          = { StateMachineArn = aws_sfn_state_machine.booking_saga[0].arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
}

# ---------------------------------------------------------------- permissions (least privilege)

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "booking_saga" {
  count = local.booking_saga_count

  name                 = local.booking_saga_service
  assume_role_policy   = data.aws_iam_policy_document.states_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "booking_saga" {
  count = local.booking_saga_count

  statement {
    sid       = "SendCommands"
    actions   = ["sqs:SendMessage"]
    resources = [module.flight_commands[0].queue_arn, module.hotel_commands[0].queue_arn]
  }

  statement {
    sid       = "PublishEvents"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.trip_events.arn]
  }

  statement {
    sid       = "Logs" # log deliveries have no resource ARN
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "booking_saga" {
  count = local.booking_saga_count

  name   = "booking-saga"
  role   = aws_iam_role.booking_saga[0].id
  policy = data.aws_iam_policy_document.booking_saga[0].json
}
