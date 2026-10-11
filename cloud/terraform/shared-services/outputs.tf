output "artifacts_bucket_name" {
  description = "Versioned bucket for Symphony build artifacts."
  value       = module.artifacts_bucket.name
}

output "artifacts_kms_key_arn" {
  description = "KMS key encrypting build artifacts."
  value       = aws_kms_key.artifacts.arn
}
