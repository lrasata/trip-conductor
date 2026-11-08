# The vendor simulator: stands in for the airline, and later the hotel and payment providers.
# API Gateway -> Lambda. It holds no data, so it's public, with a throttle as its protection.

locals {
  vendor_simulator_service = "${local.name_prefix}-vendor-simulator"
  vendor_simulator_count   = local.deploy["vendor-simulator"] ? 1 : 0
  vendor_routes            = ["POST /airline/holds", "DELETE /airline/holds/{reference}"]
}

# scripts/chaos.py writes this parameter; Terraform only creates it.
resource "aws_ssm_parameter" "chaos_airline" {
  count = local.vendor_simulator_count

  name  = "/trip-conductor/${var.environment}/chaos/airline"
  type  = "String"
  value = jsonencode({ failure_rate = 0, latency_ms = 0 })

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------- function

data "archive_file" "vendor_simulator" {
  count = local.vendor_simulator_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/vendor_simulator/build/package"
  output_path = "${path.root}/build/vendor_simulator.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "vendor_simulator" {
  count = local.vendor_simulator_count

  name              = "/aws/lambda/${local.vendor_simulator_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "vendor_simulator" {
  count = local.vendor_simulator_count

  function_name    = local.vendor_simulator_service
  role             = aws_iam_role.vendor_simulator[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "vendor_simulator_service.handler.handler"
  filename         = data.archive_file.vendor_simulator[0].output_path
  source_code_hash = data.archive_file.vendor_simulator[0].output_base64sha256
  memory_size      = 128
  timeout          = 15 # longer than any latency chaos.py is expected to inject

  environment {
    variables = {
      CHAOS_PARAMETER = aws_ssm_parameter.chaos_airline[0].name
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.vendor_simulator]
}

# ---------------------------------------------------------------- API

resource "aws_apigatewayv2_api" "vendors" {
  count = local.vendor_simulator_count

  name          = "${local.name_prefix}-vendors"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "vendors" {
  count = local.vendor_simulator_count

  api_id      = aws_apigatewayv2_api.vendors[0].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.vendor_api_rate_limit
    throttling_burst_limit = var.vendor_api_burst_limit
  }
}

resource "aws_apigatewayv2_integration" "vendors" {
  count = local.vendor_simulator_count

  api_id                 = aws_apigatewayv2_api.vendors[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.vendor_simulator[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "vendors" {
  for_each = toset(local.vendor_simulator_count == 1 ? local.vendor_routes : [])

  api_id    = aws_apigatewayv2_api.vendors[0].id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.vendors[0].id}"
}

resource "aws_lambda_permission" "vendors_api" {
  count = local.vendor_simulator_count

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.vendor_simulator[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.vendors[0].execution_arn}/*/*"
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "vendor_simulator" {
  count = local.vendor_simulator_count

  name                 = local.vendor_simulator_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "vendor_simulator" {
  count = local.vendor_simulator_count

  statement {
    sid       = "ReadChaosSettings"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.chaos_airline[0].arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.vendor_simulator[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "vendor_simulator" {
  count = local.vendor_simulator_count

  name   = "vendor-simulator"
  role   = aws_iam_role.vendor_simulator[0].id
  policy = data.aws_iam_policy_document.vendor_simulator[0].json
}
