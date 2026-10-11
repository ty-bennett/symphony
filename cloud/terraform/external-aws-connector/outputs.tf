output "integration_role_arn" {
  description = "Role the tenant document tool assumes."
  value       = aws_iam_role.integration.arn
}
