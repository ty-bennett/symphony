output "documents_bucket_name" {
  description = "Fixture bucket for cross-account connector tests."
  value       = module.documents_bucket.name
}

output "approved_prefix" {
  description = "Prefix the S3 connector is allowed to read."
  value       = local.approved_prefix
}

output "forbidden_prefix" {
  description = "Prefix every connector denial test must fail to read."
  value       = local.forbidden_prefix
}

output "documents_kms_key_arn" {
  description = "KMS key the customer-side integration role must be allowed to decrypt with."
  value       = aws_kms_key.documents.arn
}
