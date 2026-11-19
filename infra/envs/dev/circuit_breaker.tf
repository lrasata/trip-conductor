# The circuit breakers' shared state, one item per vendor (ADR 0005). Shared physically: each
# service's role is limited to its own vendor's item with dynamodb:LeadingKeys.

locals {
  breaker_count = local.deploy.flight || local.deploy.hotel || local.deploy.payment ? 1 : 0
}

resource "aws_dynamodb_table" "circuit_breakers" {
  count = local.breaker_count

  name         = "${local.name_prefix}-circuit-breakers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "vendor"

  attribute {
    name = "vendor"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- alert

# A vendor is down: CircuitOpened goes straight to the ops-alerts topic, as a readable email.
resource "aws_cloudwatch_event_rule" "circuit_opened" {
  count = local.breaker_count

  name           = "${local.name_prefix}-circuit-opened"
  description    = "A circuit breaker opened: a vendor is failing. Alerts the ops-alerts topic."
  event_bus_name = aws_cloudwatch_event_bus.trip_events.name
  event_pattern = jsonencode({
    "detail-type" = ["CircuitOpened"]
  })
}

resource "aws_cloudwatch_event_target" "circuit_opened" {
  count = local.breaker_count

  rule           = aws_cloudwatch_event_rule.circuit_opened[0].name
  event_bus_name = aws_cloudwatch_event_bus.trip_events.name
  target_id      = "ops-alerts"
  arn            = aws_sns_topic.ops_alerts.arn

  input_transformer {
    input_paths = {
      vendor    = "$.detail.data.vendor"
      failures  = "$.detail.data.failure_count"
      opened_at = "$.detail.data.opened_at"
      producer  = "$.detail.producer"
    }
    input_template = "\"Circuit breaker OPEN for vendor <vendor>: <failures> failures within the window, seen by <producer> at <opened_at>. Calls to it fail fast; after the cooldown one probe call decides whether to resume, and trips meanwhile end VENDOR_UNAVAILABLE. In dev, check the chaos settings (/trip-conductor/dev/chaos/<vendor>).\""
  }
}
