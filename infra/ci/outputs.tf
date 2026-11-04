output "plan_role_arn" {
  description = "Set as the GitHub repository variable AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "Set as the GitHub repository variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}

output "lambda_permissions_boundary_arn" {
  description = "Set as the GitHub repository variable LAMBDA_PERMISSIONS_BOUNDARY_ARN."
  value       = aws_iam_policy.lambda_boundary.arn
}
