# The projector: builds trip-view, the read model, from the trip events (ADR 0006).
# trip-events bus -> rule -> SQS projector-events -> Lambda -> trip-view

locals {
  projector_service = "${local.name_prefix}-projector"
  projector_count   = local.deploy.projector ? 1 : 0
}

module "projector_events" {
  source = "../../modules/event_queue"
  count  = local.projector_count

  name_prefix     = local.name_prefix
  service_name    = "projector"
  description     = "Every trip event, for the read model. Not the circuit breakers' events, which have no trip."
  event_bus_name  = aws_cloudwatch_event_bus.trip_events.name
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
  event_pattern = {
    source        = [{ prefix = "trip-conductor." }]
    "detail-type" = [{ "anything-but" = ["CircuitOpened", "CircuitClosed"] }]
  }
}

# One item per trip. Rebuildable from the event archive at any time (scripts/rebuild_read_model.py),
# so it needs no backup of its own.
resource "aws_dynamodb_table" "trip_view" {
  count = local.projector_count

  name         = "${local.name_prefix}-trip-view"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "trip_id"

  attribute {
    name = "trip_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  # "My trips", newest first. A view only joins it once TripRequested has given it both keys.
  global_secondary_index {
    name            = "by-customer"
    hash_key        = "customer_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- function

data "archive_file" "projector" {
  count = local.projector_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/projector/build/package"
  output_path = "${path.root}/build/projector.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "projector" {
  count = local.projector_count

  name              = "/aws/lambda/${local.projector_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "projector" {
  count = local.projector_count

  function_name    = local.projector_service
  role             = aws_iam_role.projector[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "projector_service.handler.handler"
  filename         = data.archive_file.projector[0].output_path
  source_code_hash = data.archive_file.projector[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.trip_view[0].name
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.projector]
}

resource "aws_lambda_event_source_mapping" "projector" {
  count = local.projector_count

  event_source_arn        = module.projector_events[0].queue_arn
  function_name           = aws_lambda_function.projector[0].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "projector" {
  count = local.projector_count

  name                 = local.projector_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "projector" {
  count = local.projector_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.projector_events[0].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.trip_view[0].arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.projector[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "projector" {
  count = local.projector_count

  name   = "projector"
  role   = aws_iam_role.projector[0].id
  policy = data.aws_iam_policy_document.projector[0].json
}
