# Per-tenant deployment of cloud/tools/customer_documents. It assumes one
# customer-owned role with a tenant-specific external ID; bucket, prefix, and
# role come from deployment state, never from model arguments.
resource "random_uuid" "external_id" {}

data "archive_file" "documents_tool" {
  type        = "zip"
  source_file = "${path.module}/../../tools/customer_documents/handler.py"
  output_path = "${path.module}/.build/documents_tool.zip"
}

resource "aws_cloudwatch_log_group" "documents_tool" {
  name              = "/symphony/${var.tenant_id}/documents-tool"
  retention_in_days = 30
  kms_key_id        = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_iam_role" "documents_tool" {
  name = "${local.name_prefix}-documents-tool"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_role_policy" "documents_tool" {
  name = "documents-tool"
  role = aws_iam_role.documents_tool.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeCustomerIntegrationRole"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = local.external_role
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.documents_tool.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "documents_tool" {
  function_name    = "${local.name_prefix}-documents-tool"
  role             = aws_iam_role.documents_tool.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.documents_tool.output_path
  source_code_hash = data.archive_file.documents_tool.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      DOCS_BUCKET         = local.fixture.documents_bucket_name
      DOCS_PREFIX         = local.fixture.approved_prefix
      EXTERNAL_ROLE_ARN   = local.external_role
      EXTERNAL_ID         = random_uuid.external_id.result
      EXPECTED_GATEWAY_ID = aws_bedrockagentcore_gateway.documents.gateway_id
      GATEWAY_TARGET_NAME = local.tool_target_name
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.documents_tool.name
  }

  depends_on = [aws_iam_role_policy.documents_tool]
}
