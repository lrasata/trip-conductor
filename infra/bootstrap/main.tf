# One-time setup: the S3 bucket and DynamoDB lock table for Terraform state.
# Applied once with local state; everything else then uses these as its backend.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "trip-conductor"
      ManagedBy = "terraform"
      Component = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "state" {
  # Account ID in the name keeps it globally unique without guessing.
  bucket = "trip-conductor-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled" # lets you recover an earlier state file
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_dynamodb_table" "state_lock" {
  name         = "${aws_s3_bucket.state.bucket}-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

variable "region" {
  description = "AWS region for the state bucket and lock table (use the same region as the environments)."
  type        = string
  default     = "eu-central-1"
}

output "state_bucket" {
  description = "Put this in infra/envs/dev/backend.hcl as 'bucket'."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "Put this in infra/envs/dev/backend.hcl as 'dynamodb_table'."
  value       = aws_dynamodb_table.state_lock.name
}
