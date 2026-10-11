module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [local.home_region]

  depends_on = [terraform_data.account_guard]
}

# Holds versioned build artifacts (Lambda packages, edge images). No tenant
# prompts, documents, or connector credentials belong here.
resource "aws_kms_key" "artifacts" {
  description             = "Encrypts Symphony shared build artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableSharedServicesIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.shared_services_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_alias" "artifacts" {
  name          = "alias/symphony/artifacts"
  target_key_id = aws_kms_key.artifacts.key_id
}

module "artifacts_bucket" {
  source = "../modules/secure-bucket"

  name        = "symphony-artifacts-${var.shared_services_account_id}"
  kms_key_arn = aws_kms_key.artifacts.arn
}
