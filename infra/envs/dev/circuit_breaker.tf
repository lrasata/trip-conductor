# The circuit breakers' shared state, one item per vendor (ADR 0005). Shared physically: each
# service's role is limited to its own vendor's item with dynamodb:LeadingKeys.

locals {
  breaker_count = local.deploy.flight || local.deploy.hotel ? 1 : 0
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
