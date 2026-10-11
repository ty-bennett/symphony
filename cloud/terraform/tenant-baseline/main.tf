locals {
  name_prefix    = "symphony-${var.tenant_id}"
  log_group_name = "/symphony/${var.tenant_id}/application"
}

module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [var.workload_region]

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_key" "tenant_data" {
  description             = "Encrypts ${var.tenant_id} tenant data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableTenantAccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.tenant_account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowSymphonyLogGroups"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.workload_region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.workload_region}:${var.tenant_account_id}:log-group:/symphony/${var.tenant_id}/*"
          }
        }
      },
    ]
  })

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_alias" "tenant_data" {
  name          = "alias/symphony/${var.tenant_id}/data"
  target_key_id = aws_kms_key.tenant_data.key_id
}

resource "aws_cloudwatch_log_group" "application" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.tenant_data.arn
}

# One item per issued device certificate, so overlapping certificates can
# coexist during rotation. The Phase 3 authorizer looks up by serial.
resource "aws_dynamodb_table" "device_registry" {
  name                        = "${local.name_prefix}-device-registry"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "cert_serial"
  deletion_protection_enabled = true

  attribute {
    name = "cert_serial"
    type = "S"
  }

  attribute {
    name = "device_id"
    type = "S"
  }

  global_secondary_index {
    name            = "by_device_id"
    projection_type = "ALL"

    key_schema {
      attribute_name = "device_id"
      key_type       = "HASH"
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.tenant_data.arn
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Pilot end-user identities. Administrators create users; there is no self sign-up.
resource "aws_cognito_user_pool" "users" {
  name                     = "${local.name_prefix}-users"
  user_pool_tier           = "LITE"
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "ON"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  # Keep the old address active until a changed email is verified.
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}
