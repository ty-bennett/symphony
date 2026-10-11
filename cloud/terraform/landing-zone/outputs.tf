output "landing_zone_arn" {
  description = "Control Tower landing zone ARN."
  value       = aws_controltower_landing_zone.this.arn
}

output "landing_zone_version" {
  description = "Deployed landing zone version."
  value       = aws_controltower_landing_zone.this.version
}

output "governed_regions" {
  description = "Regions governed by Control Tower."
  value       = local.governed_regions
}

output "controltower_logs_kms_key_arn" {
  description = "KMS key encrypting Control Tower logs."
  value       = aws_kms_key.controltower_logs.arn
}
