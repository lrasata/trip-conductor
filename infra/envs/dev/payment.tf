# The payment service: the saga's pivot.
# SQS payment-commands -> Lambda -> payment provider (through the breaker) -> task token + trip-events

locals {
  payment_service = "${local.name_prefix}-payment"
  payment_count   = local.deploy.payment ? 1 : 0
}

module "payment_commands" {
  source = "../../modules/command_queue"
  count  = local.payment_count

  name_prefix     = local.name_prefix
  service_name    = "payment"
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
}

resource "aws_dynamodb_table" "payments" {
  count = local.payment_count

  name         = "${local.name_prefix}-payments"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "trip_id"

  attribute {
    name = "trip_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- function

data "archive_file" "payment" {
  count = local.payment_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/payment/build/package"
  output_path = "${path.root}/build/payment.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "payment" {
  count = local.payment_count

  name              = "/aws/lambda/${local.payment_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "payment" {
  count = local.payment_count

  function_name    = local.payment_service
  role             = aws_iam_role.payment[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "payment_service.handler.handler"
  filename         = data.archive_file.payment[0].output_path
  source_code_hash = data.archive_file.payment[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME         = aws_dynamodb_table.payments[0].name
      BREAKER_TABLE_NAME = aws_dynamodb_table.circuit_breakers[0].name
      EVENT_BUS_NAME     = aws_cloudwatch_event_bus.trip_events.name
      VENDOR_URL         = aws_apigatewayv2_stage.vendors[0].invoke_url
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.payment]
}

resource "aws_lambda_event_source_mapping" "payment" {
  count = local.payment_count

  event_source_arn        = module.payment_commands[0].queue_arn
  function_name           = aws_lambda_function.payment[0].arn
  batch_size              = 1 # a command waiting behind a slow vendor call would eat its saga's timeout
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "payment" {
  count = local.payment_count

  name                 = local.payment_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "payment" {
  count = local.payment_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.payment_commands[0].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.payments[0].arn]
  }

  statement {
    sid       = "OwnBreakerItem"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.circuit_breakers[0].arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["payment"]
    }
  }

  statement {
    sid       = "AnswerTheSaga"
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
    resources = [local.booking_saga_arn]
  }

  statement {
    sid       = "PublishEvents"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.trip_events.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.payment[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "payment" {
  count = local.payment_count

  name   = "payment-service"
  role   = aws_iam_role.payment[0].id
  policy = data.aws_iam_policy_document.payment[0].json
}
