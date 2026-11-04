terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Same state bucket as the environments, different key:
  #   terraform init -backend-config=../envs/dev/backend.hcl
  backend "s3" {
    key     = "ci/terraform.tfstate"
    encrypt = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "trip-conductor"
      ManagedBy = "terraform"
      Component = "ci"
    }
  }
}
