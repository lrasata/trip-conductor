variable "region" {
  description = "AWS region the environments are deployed to."
  type        = string
  default     = "eu-central-1"
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the CI roles, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "deploy_environment" {
  description = "GitHub environment whose jobs may deploy. Its protection rules (e.g. required reviewers) gate every apply."
  type        = string
  default     = "dev"
}

variable "create_oidc_provider" {
  description = "Set to false if the account already has the GitHub OIDC provider (only one per account is allowed)."
  type        = bool
  default     = true
}

variable "github_subject_prefix" {
  description = <<-EOT
    Start of the OIDC "sub" claim GitHub sends for this repository. Leave null for the classic
    "repo:owner/name". Repositories using GitHub's immutable subject send
    "repo:owner@<owner-id>/name@<repo-id>" instead; read yours with
    GET https://api.github.com/repos/<owner>/<name>/actions/oidc/customization/sub (sub_claim_prefix).
  EOT
  type        = string
  default     = null

  validation {
    condition     = var.github_subject_prefix == null || startswith(coalesce(var.github_subject_prefix, "x"), "repo:")
    error_message = "github_subject_prefix must start with \"repo:\"."
  }
}
