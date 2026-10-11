# Account-wide defaults every Symphony member account gets. The
# baseline-protection SCP stops anyone but AWSControlTowerExecution reverting them.
resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ebs_encryption_by_default" "this" {
  for_each = var.regions

  region  = each.value
  enabled = true
}

resource "aws_accessanalyzer_analyzer" "this" {
  for_each = var.regions

  region        = each.value
  analyzer_name = "symphony-external-access"
  type          = "ACCOUNT"
}
