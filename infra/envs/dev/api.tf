# The trips API: POST /trips (trip-commands) and GET /trips (trip-queries).
# A REST API rather than an HTTP API because it needs API keys and a usage plan: customers
# aren't authenticated (out of scope), so the key is what keeps the API from being public.

locals {
  api_count = local.deploy["trip-commands"] ? 1 : 0 # trip-queries adds its routes to it
  api_stage = "v1"
}

resource "aws_api_gateway_rest_api" "trips" {
  count = local.api_count

  name        = "${local.name_prefix}-trips"
  description = "Book a trip and follow it."

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "trips" {
  count = local.api_count

  rest_api_id = aws_api_gateway_rest_api.trips[0].id
  parent_id   = aws_api_gateway_rest_api.trips[0].root_resource_id
  path_part   = "trips"
}

# A deployment is a snapshot: a new one is made whenever a route changes.
resource "aws_api_gateway_deployment" "trips" {
  count = local.api_count

  rest_api_id = aws_api_gateway_rest_api.trips[0].id

  triggers = {
    routes = sha1(jsonencode([
      aws_api_gateway_resource.trips,
      aws_api_gateway_method.post_trips,
      aws_api_gateway_integration.post_trips,
      aws_api_gateway_resource.trip,
      aws_api_gateway_method.get_trips,
      aws_api_gateway_integration.get_trips,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [aws_api_gateway_integration.post_trips, aws_api_gateway_integration.get_trips]
}

resource "aws_api_gateway_stage" "trips" {
  count = local.api_count

  rest_api_id          = aws_api_gateway_rest_api.trips[0].id
  deployment_id        = aws_api_gateway_deployment.trips[0].id
  stage_name           = local.api_stage
  xray_tracing_enabled = true
}

# ---------------------------------------------------------------- API key

resource "aws_api_gateway_api_key" "trips" {
  count = local.api_count

  name = "${local.name_prefix}-trips"
}

resource "aws_api_gateway_usage_plan" "trips" {
  count = local.api_count

  name = "${local.name_prefix}-trips"

  api_stages {
    api_id = aws_api_gateway_rest_api.trips[0].id
    stage  = aws_api_gateway_stage.trips[0].stage_name
  }

  throttle_settings {
    rate_limit  = var.trips_api_rate_limit
    burst_limit = var.trips_api_burst_limit
  }

  quota_settings {
    limit  = var.trips_api_daily_quota
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "trips" {
  count = local.api_count

  key_id        = aws_api_gateway_api_key.trips[0].id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.trips[0].id
}
