output "management_account_id" {
  description = "Symphony management account ID."
  value       = var.management_account_id
}

output "organization_id" {
  description = "AWS Organizations ID."
  value       = aws_organizations_organization.this.id
}

output "organization_root_id" {
  description = "Root ID that workload OUs are created under."
  value       = aws_organizations_organization.this.roots[0].id
}

output "security_ou_id" {
  description = "Security OU holding the log archive and audit accounts."
  value       = aws_organizations_organizational_unit.security.id
}

output "log_archive_account_id" {
  description = "Log archive account ID."
  value       = aws_organizations_account.log_archive.id
}

output "audit_account_id" {
  description = "Audit (security tooling) account ID."
  value       = aws_organizations_account.audit.id
}

output "state_bucket_name" {
  description = "S3 bucket holding Terraform state for every root."
  value       = module.state_bucket.name
}

output "state_kms_key_arn" {
  description = "KMS key that encrypts Terraform state."
  value       = aws_kms_key.state.arn
}
