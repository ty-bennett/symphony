locals {
  bucket_arn = "arn:aws:s3:::${local.fixture.documents_bucket_name}"
  prefix     = local.fixture.approved_prefix
}

# The external ID is an extra trust condition, not a secret: the exact tenant
# principal is what grants access. Remove this role to revoke Symphony.
resource "aws_iam_role" "integration" {
  name                 = "SymphonyIntegrationRole"
  description          = "Read-only access for the Symphony ${var.tenant_id} document tool"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = local.tenant.documents_tool_role_arn }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "sts:ExternalId" = local.tenant.external_id } }
    }]
  })

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_role_policy" "integration" {
  name = "approved-documents-read-only"
  role = aws_iam_role.integration.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "ListApprovedPrefixOnly"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = local.bucket_arn
        Condition = { StringLike = { "s3:prefix" = [local.prefix, "${local.prefix}*"] } }
      },
      {
        Sid      = "ReadApprovedObjectsOnly"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${local.bucket_arn}/${local.prefix}*"
      },
      {
        Sid       = "DecryptThroughS3Only"
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = local.fixture.documents_kms_key_arn
        Condition = { StringEquals = { "kms:ViaService" = "s3.${local.home_region}.amazonaws.com" } }
      },
    ]
  })
}
