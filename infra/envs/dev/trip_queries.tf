# trip-queries: GET /trips/{trip_id} and GET /trips?customer_id= (the query side of CQRS).
# API Gateway -> Lambda -> trip-view (read only)

locals {
  trip_queries_service = "${local.name_prefix}-trip-queries"
  trip_queries_count   = local.deploy["trip-queries"] ? 1 : 0
}

# ---------------------------------------------------------------- function

data "archive_file" "trip_queries" {
  count = local.trip_queries_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/trip_queries/build/package"
  output_path = "${path.root}/build/trip_queries.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "trip_queries" {
  count = local.trip_queries_count

  name              = "/aws/lambda/${local.trip_queries_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "trip_queries" {
  count = local.trip_queries_count

  function_name    = local.trip_queries_service
  role             = aws_iam_role.trip_queries[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "trip_queries_service.handler.handler"
  filename         = data.archive_file.trip_queries[0].output_path
  source_code_hash = data.archive_file.trip_queries[0].output_base64sha256
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.trip_view[0].name
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.trip_queries]
}

# ---------------------------------------------------------------- routes: GET /trips, GET /trips/{trip_id}

resource "aws_api_gateway_resource" "trip" {
  count = local.trip_queries_count

  rest_api_id = aws_api_gateway_rest_api.trips[0].id
  parent_id   = aws_api_gateway_resource.trips[0].id
  path_part   = "{trip_id}"
}

locals {
  # route -> API Gateway resource it hangs off
  trip_query_routes = local.trip_queries_count == 1 ? {
    list = aws_api_gateway_resource.trips[0].id
    get  = aws_api_gateway_resource.trip[0].id
  } : {}
}

resource "aws_api_gateway_method" "get_trips" {
  for_each = local.trip_query_routes

  rest_api_id      = aws_api_gateway_rest_api.trips[0].id
  resource_id      = each.value
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = true
}

resource "aws_api_gateway_integration" "get_trips" {
  for_each = local.trip_query_routes

  rest_api_id             = aws_api_gateway_rest_api.trips[0].id
  resource_id             = each.value
  http_method             = aws_api_gateway_method.get_trips[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST" # Lambda is always invoked with POST, whatever the route's method
  uri                     = aws_lambda_function.trip_queries[0].invoke_arn
}

resource "aws_lambda_permission" "get_trips" {
  count = local.trip_queries_count

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trip_queries[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.trips[0].execution_arn}/*/GET/trips*"
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "trip_queries" {
  count = local.trip_queries_count

  name                 = local.trip_queries_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "trip_queries" {
  count = local.trip_queries_count

  statement {
    sid       = "ReadTheView"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.trip_view[0].arn, "${aws_dynamodb_table.trip_view[0].arn}/index/by-customer"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.trip_queries[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "trip_queries" {
  count = local.trip_queries_count

  name   = "trip-queries"
  role   = aws_iam_role.trip_queries[0].id
  policy = data.aws_iam_policy_document.trip_queries[0].json
}
