# Stand-in for a customer's existing AWS account: one bucket with an approved
# prefix the S3 connector may read and a forbidden prefix it must never return.
# Every object is synthetic; never upload real customer documents here.
locals {
  approved_prefix  = "approved-docs/"
  forbidden_prefix = "forbidden-docs/"
  fixture_files    = fileset("${path.module}/fixtures", "**")
  content_types = {
    csv = "text/csv"
    md  = "text/markdown"
    txt = "text/plain"
  }
}

module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [local.home_region]

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_key" "documents" {
  description             = "Encrypts synthetic fixture documents"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableFixtureAccountIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.fixture_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  depends_on = [terraform_data.account_guard]
}

module "documents_bucket" {
  source = "../modules/secure-bucket"

  name                               = "symphony-fixture-${var.fixture_account_id}-docs"
  kms_key_arn                        = aws_kms_key.documents.arn
  noncurrent_version_expiration_days = 7
}

resource "aws_s3_object" "fixture" {
  for_each = local.fixture_files

  bucket       = module.documents_bucket.name
  key          = each.value
  source       = "${path.module}/fixtures/${each.value}"
  source_hash  = filemd5("${path.module}/fixtures/${each.value}")
  content_type = local.content_types[reverse(split(".", each.value))[0]]
  kms_key_id   = aws_kms_key.documents.arn
}
