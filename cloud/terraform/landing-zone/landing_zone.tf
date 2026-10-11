locals {
  log_bucket_configuration = {
    loggingBucket       = { retentionDays = var.log_retention_days }
    accessLoggingBucket = { retentionDays = var.access_log_retention_days }
    kmsKeyArn           = aws_kms_key.controltower_logs.arn
  }

  # Landing zone 4.0 manifest: every integration needs an explicit enabled flag.
  # The audit account holds security roles and the Config aggregator; backup is off for the pilot.
  landing_zone_manifest = {
    governedRegions  = local.governed_regions
    accessManagement = { enabled = true }
    backup           = { enabled = false }
    centralizedLogging = {
      enabled        = true
      accountId      = local.bootstrap.log_archive_account_id
      configurations = local.log_bucket_configuration
    }
    config = {
      enabled        = true
      accountId      = local.bootstrap.audit_account_id
      configurations = local.log_bucket_configuration
    }
    securityRoles = {
      enabled   = true
      accountId = local.bootstrap.audit_account_id
    }
  }
}

resource "aws_controltower_landing_zone" "this" {
  manifest_json = jsonencode(local.landing_zone_manifest)
  version       = var.landing_zone_version

  timeouts {
    create = "120m"
    update = "120m"
    delete = "120m"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}
