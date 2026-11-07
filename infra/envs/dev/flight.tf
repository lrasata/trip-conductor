# The flight service.
# SQS flight-commands -> Lambda -> airline (through the breaker) -> task token + trip-events

locals {
  flight_service = "${local.name_prefix}-flight"
  flight_count   = local.deploy.flight ? 1 : 0
}

module "flight_commands" {
  source = "../../modules/command_queue"
  count  = local.flight_count

  name_prefix     = local.name_prefix
  service_name    = "flight"
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
}

resource "aws_dynamodb_table" "flight_reservations" {
  count = local.flight_count

  name         = "${local.name_prefix}-flight-reservations"
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

data "archive_file" "flight" {
  count = local.flight_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/flight/build/package"
  output_path = "${path.root}/build/flight.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "flight" {
  count = local.flight_count

  name              = "/aws/lambda/${local.flight_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "flight" {
  count = local.flight_count

  function_name    = local.flight_service
  role             = aws_iam_role.flight[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "flight_service.handler.handler"
  filename         = data.archive_file.flight[0].output_path
  source_code_hash = data.archive_file.flight[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME         = aws_dynamodb_table.flight_reservations[0].name
      BREAKER_TABLE_NAME = aws_dynamodb_table.circuit_breakers[0].name
      EVENT_BUS_NAME     = aws_cloudwatch_event_bus.trip_events.name
      VENDOR_URL         = aws_apigatewayv2_stage.vendors[0].invoke_url
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.flight]
}

resource "aws_lambda_event_source_mapping" "flight" {
  count = local.flight_count

  event_source_arn        = module.flight_commands[0].queue_arn
  function_name           = aws_lambda_function.flight[0].arn
  batch_size              = 1 # a command waiting behind a slow vendor call would eat its saga's timeout
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "flight" {
  count = local.flight_count

  name                 = local.flight_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "flight" {
  count = local.flight_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.flight_commands[0].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.flight_reservations[0].arn]
  }

  statement {
    sid       = "OwnBreakerItem"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.circuit_breakers[0].arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["airline"]
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
    resources = ["${aws_cloudwatch_log_group.flight[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "flight" {
  count = local.flight_count

  name   = "flight-service"
  role   = aws_iam_role.flight[0].id
  policy = data.aws_iam_policy_document.flight[0].json
}
