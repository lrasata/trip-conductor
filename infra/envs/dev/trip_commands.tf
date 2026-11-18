# trip-commands: POST /trips starts a booking saga (the command side of CQRS).
# API Gateway -> Lambda -> Step Functions StartExecution

locals {
  trip_commands_service = "${local.name_prefix}-trip-commands"
  trip_commands_count   = local.deploy["trip-commands"] ? 1 : 0
}

# ---------------------------------------------------------------- function

data "archive_file" "trip_commands" {
  count = local.trip_commands_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/trip_commands/build/package"
  output_path = "${path.root}/build/trip_commands.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "trip_commands" {
  count = local.trip_commands_count

  name              = "/aws/lambda/${local.trip_commands_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "trip_commands" {
  count = local.trip_commands_count

  function_name    = local.trip_commands_service
  role             = aws_iam_role.trip_commands[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "trip_commands_service.handler.handler"
  filename         = data.archive_file.trip_commands[0].output_path
  source_code_hash = data.archive_file.trip_commands[0].output_base64sha256
  memory_size      = 128
  timeout          = 10 # API Gateway gives up after 29 s; StartExecution takes milliseconds

  environment {
    variables = {
      STATE_MACHINE_ARN = local.booking_saga_arn
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.trip_commands]
}

# ---------------------------------------------------------------- route: POST /trips

resource "aws_api_gateway_method" "post_trips" {
  count = local.trip_commands_count

  rest_api_id      = aws_api_gateway_rest_api.trips[0].id
  resource_id      = aws_api_gateway_resource.trips[0].id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true
}

resource "aws_api_gateway_integration" "post_trips" {
  count = local.trip_commands_count

  rest_api_id             = aws_api_gateway_rest_api.trips[0].id
  resource_id             = aws_api_gateway_resource.trips[0].id
  http_method             = aws_api_gateway_method.post_trips[0].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.trip_commands[0].invoke_arn
}

resource "aws_lambda_permission" "post_trips" {
  count = local.trip_commands_count

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trip_commands[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.trips[0].execution_arn}/*/POST/trips"
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "trip_commands" {
  count = local.trip_commands_count

  name                 = local.trip_commands_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "trip_commands" {
  count = local.trip_commands_count

  statement {
    sid       = "StartBookings"
    actions   = ["states:StartExecution"]
    resources = [local.booking_saga_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.trip_commands[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "trip_commands" {
  count = local.trip_commands_count

  name   = "trip-commands"
  role   = aws_iam_role.trip_commands[0].id
  policy = data.aws_iam_policy_document.trip_commands[0].json
}
