# Notification: emails the customer when a trip ends (publish-subscribe, outside the saga).
# trip-events bus -> rule -> SQS notification-events -> Lambda -> SES

locals {
  notification_service = "${local.name_prefix}-notification"
  notification_count   = local.deploy.notification ? 1 : 0
}

module "notification_events" {
  source = "../../modules/event_queue"
  count  = local.notification_count

  name_prefix     = local.name_prefix
  service_name    = "notification"
  description     = "Trips that ended confirmed or cancelled. A rebuild never replays to this rule."
  event_bus_name  = aws_cloudwatch_event_bus.trip_events.name
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
  event_pattern = {
    source        = ["trip-conductor.booking-saga"]
    "detail-type" = ["TripConfirmed", "TripCancelled"]
  }
}

# One item per trip: what was sent, so a redelivery never sends twice.
resource "aws_dynamodb_table" "notifications" {
  count = local.notification_count

  name         = "${local.name_prefix}-notifications"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "trip_id"

  attribute {
    name = "trip_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# Stands in for a customer service: customer_id -> email. Filled with scripts/add_contact.py,
# not Terraform: the plan role may not read customer data, so it couldn't refresh an item.
resource "aws_dynamodb_table" "customer_contacts" {
  count = local.notification_count

  name         = "${local.name_prefix}-customer-contacts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "customer_id"

  attribute {
    name = "customer_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# The sender. SES starts in its sandbox, where every recipient must be verified too: the demo
# customer's contact is this same address. AWS emails it a link to click once.
resource "aws_ses_email_identity" "sender" {
  count = local.notification_count

  email = var.alert_email
}

# ---------------------------------------------------------------- function

data "archive_file" "notification" {
  count = local.notification_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/notification/build/package"
  output_path = "${path.root}/build/notification.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "notification" {
  count = local.notification_count

  name              = "/aws/lambda/${local.notification_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "notification" {
  count = local.notification_count

  function_name    = local.notification_service
  role             = aws_iam_role.notification[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "notification_service.handler.handler"
  filename         = data.archive_file.notification[0].output_path
  source_code_hash = data.archive_file.notification[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.notifications[0].name
      CONTACTS_TABLE_NAME = aws_dynamodb_table.customer_contacts[0].name
      SENDER_EMAIL        = aws_ses_email_identity.sender[0].email
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.notification]
}

resource "aws_lambda_event_source_mapping" "notification" {
  count = local.notification_count

  event_source_arn        = module.notification_events[0].queue_arn
  function_name           = aws_lambda_function.notification[0].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2 # SES sandbox: 1 email per second
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "notification" {
  count = local.notification_count

  name                 = local.notification_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "notification" {
  count = local.notification_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.notification_events[0].queue_arn]
  }

  statement {
    sid       = "OwnNotifications"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.notifications[0].arn]
  }

  statement {
    sid       = "ReadContacts"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.customer_contacts[0].arn]
  }

  # In the sandbox SES also checks the recipient's identity, so the resource can't be just the
  # sender's. What's pinned is who the email is from.
  statement {
    sid       = "SendAsTheSender"
    actions   = ["ses:SendEmail"]
    resources = ["arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}:identity/*"]

    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.alert_email]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.notification[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "notification" {
  count = local.notification_count

  name   = "notification"
  role   = aws_iam_role.notification[0].id
  policy = data.aws_iam_policy_document.notification[0].json
}
