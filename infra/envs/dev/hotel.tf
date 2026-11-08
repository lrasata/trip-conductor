# The hotel service.
# SQS hotel-commands -> Lambda -> hotel provider (through the breaker) -> task token + trip-events

locals {
  hotel_service = "${local.name_prefix}-hotel"
  hotel_count   = local.deploy.hotel ? 1 : 0
}

module "hotel_commands" {
  source = "../../modules/command_queue"
  count  = local.hotel_count

  name_prefix     = local.name_prefix
  service_name    = "hotel"
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
}

resource "aws_dynamodb_table" "hotel_reservations" {
  count = local.hotel_count

  name         = "${local.name_prefix}-hotel-reservations"
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

data "archive_file" "hotel" {
  count = local.hotel_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/hotel/build/package"
  output_path = "${path.root}/build/hotel.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "hotel" {
  count = local.hotel_count

  name              = "/aws/lambda/${local.hotel_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "hotel" {
  count = local.hotel_count

  function_name    = local.hotel_service
  role             = aws_iam_role.hotel[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "hotel_service.handler.handler"
  filename         = data.archive_file.hotel[0].output_path
  source_code_hash = data.archive_file.hotel[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME         = aws_dynamodb_table.hotel_reservations[0].name
      BREAKER_TABLE_NAME = aws_dynamodb_table.circuit_breakers[0].name
      EVENT_BUS_NAME     = aws_cloudwatch_event_bus.trip_events.name
      VENDOR_URL         = aws_apigatewayv2_stage.vendors[0].invoke_url
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.hotel]
}

resource "aws_lambda_event_source_mapping" "hotel" {
  count = local.hotel_count

  event_source_arn        = module.hotel_commands[0].queue_arn
  function_name           = aws_lambda_function.hotel[0].arn
  batch_size              = 1 # a command waiting behind a slow vendor call would eat its saga's timeout
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "hotel" {
  count = local.hotel_count

  name                 = local.hotel_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "hotel" {
  count = local.hotel_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.hotel_commands[0].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.hotel_reservations[0].arn]
  }

  statement {
    sid       = "OwnBreakerItem"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.circuit_breakers[0].arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["hotel"]
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
    resources = ["${aws_cloudwatch_log_group.hotel[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "hotel" {
  count = local.hotel_count

  name   = "hotel-service"
  role   = aws_iam_role.hotel[0].id
  policy = data.aws_iam_policy_document.hotel[0].json
}
