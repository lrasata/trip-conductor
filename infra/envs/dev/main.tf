locals {
  name_prefix = "trip-conductor-${var.environment}"

  # In deployment order: the vendors first, then the services the saga commands, then what
  # reacts to its events.
  all_services = ["vendor-simulator", "flight", "hotel", "payment", "booking-saga", "trip-commands", "projector", "trip-queries", "notification"]

  # Which services are deployed (var.services).
  deploy = { for service in local.all_services : service => contains(var.services, service) }
}

data "aws_caller_identity" "current" {}

# Shared by every service's Lambda role.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------- events

resource "aws_cloudwatch_event_bus" "trip_events" {
  name = "${local.name_prefix}-trip-events"
}

# Everything on the bus, kept forever: the read model is rebuilt from it (ADR 0004, 0006).
resource "aws_cloudwatch_event_archive" "trip_events" {
  name             = "${local.name_prefix}-trip-events"
  event_source_arn = aws_cloudwatch_event_bus.trip_events.arn
  description      = "Every event on the trip-events bus, replayed to rebuild the read model."
  retention_days   = 0
}

# ---------------------------------------------------------------- alerts

# Where alarms and alerts go.
# Not KMS-encrypted on purpose: CloudWatch can't publish to a topic encrypted with the
# AWS-managed SNS key (it would need a customer-managed key).
resource "aws_sns_topic" "ops_alerts" {
  name = "${local.name_prefix}-ops-alerts"
}

resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS sends a confirmation email that must be clicked once
}

# ---------------------------------------------------------------- cost guardrail

# Account-wide on purpose: in a personal account it also catches anything created outside Terraform.

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
