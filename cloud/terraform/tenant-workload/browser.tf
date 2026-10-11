# Managed web browser limited to allowlisted sites by a Chrome enterprise
# policy, with every session recorded to an encrypted, expiring bucket.
locals {
  browser_bucket = "symphony-${var.tenant_id}-browser-${var.tenant_account_id}"
  browser_policy = {
    URLBlocklist           = ["*"]
    URLAllowlist           = var.browser_allowed_domains
    PasswordManagerEnabled = false
    AutofillAddressEnabled = false
    DownloadRestrictions   = 3
  }
}

module "browser_bucket" {
  source = "../modules/secure-bucket"

  name                               = local.browser_bucket
  kms_key_arn                        = local.tenant_baseline.tenant_data_kms_key_arn
  noncurrent_version_expiration_days = 7
}

resource "aws_s3_bucket_lifecycle_configuration" "recordings_expiry" {
  bucket = module.browser_bucket.name

  rule {
    id     = "expire-recordings"
    status = "Enabled"

    filter {
      prefix = "recordings/"
    }

    expiration {
      days = 30
    }
  }
}

resource "aws_s3_object" "browser_policy" {
  bucket       = module.browser_bucket.name
  key          = "policies/managed.json"
  content      = jsonencode(local.browser_policy)
  content_type = "application/json"
  kms_key_id   = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_iam_role" "browser" {
  name = "${local.name_prefix}-browser"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.tenant_account_id } }
    }]
  })

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_role_policy" "browser" {
  name = "browser-recordings"
  role = aws_iam_role.browser.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteRecordings"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${module.browser_bucket.arn}/recordings/*"
      },
      {
        Sid      = "ReadPolicy"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${module.browser_bucket.arn}/policies/*"
      },
      {
        Sid      = "UseTenantKey"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = local.tenant_baseline.tenant_data_kms_key_arn
      },
    ]
  })
}

resource "aws_bedrockagentcore_browser" "web" {
  name               = "symphony_${replace(var.tenant_id, "-", "_")}_browser"
  description        = "Allowlisted, recorded browser for the ${var.tenant_id} agent"
  execution_role_arn = aws_iam_role.browser.arn

  network_configuration {
    network_mode = "PUBLIC"
  }

  recording {
    enabled = true

    s3_location {
      bucket = module.browser_bucket.name
      prefix = "recordings/"
    }
  }

  enterprise_policy {
    type = "MANAGED"

    location {
      s3 {
        bucket = module.browser_bucket.name
        prefix = aws_s3_object.browser_policy.key
      }
    }
  }

  depends_on = [aws_iam_role_policy.browser]
}
