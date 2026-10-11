locals {
  state_bucket_name = "symphony-tfstate-${var.management_account_id}"
}

resource "aws_kms_key" "state" {
  description             = "Encrypts Symphony Terraform state"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableManagementAccountIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.management_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/symphony/terraform-state"
  target_key_id = aws_kms_key.state.key_id
}

module "state_bucket" {
  source = "../modules/secure-bucket"

  name        = local.state_bucket_name
  kms_key_arn = aws_kms_key.state.arn
  additional_policy_statements = [{
    Sid       = "DenyPrincipalsOutsideManagementAccount"
    Effect    = "Deny"
    Principal = "*"
    Action    = "s3:*"
    Resource  = ["arn:aws:s3:::${local.state_bucket_name}", "arn:aws:s3:::${local.state_bucket_name}/*"]
    Condition = { StringNotEquals = { "aws:PrincipalAccount" = var.management_account_id } }
  }]
}
