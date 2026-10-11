output "tenant_data_kms_key_arn" {
  description = "KMS key for tenant data at rest."
  value       = aws_kms_key.tenant_data.arn
}

output "device_registry_table_name" {
  description = "DynamoDB table of issued device certificates."
  value       = aws_dynamodb_table.device_registry.name
}

output "device_registry_table_arn" {
  description = "ARN of the device registry table."
  value       = aws_dynamodb_table.device_registry.arn
}

output "user_pool_id" {
  description = "Cognito user pool for pilot end users."
  value       = aws_cognito_user_pool.users.id
}

output "user_pool_arn" {
  description = "ARN of the pilot user pool."
  value       = aws_cognito_user_pool.users.arn
}

output "application_log_group_name" {
  description = "Encrypted log group for tenant application logs."
  value       = aws_cloudwatch_log_group.application.name
}
