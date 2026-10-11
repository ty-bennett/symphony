resource "aws_iam_role" "gateway" {
  name = "${local.name_prefix}-gateway"
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

resource "aws_iam_role_policy" "gateway" {
  name = "invoke-documents-tool"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeOnlyTheDocumentTool"
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.documents_tool.arn
    }]
  })
}

resource "aws_bedrockagentcore_gateway" "documents" {
  name            = "${local.name_prefix}-gateway"
  description     = "Approved tools for the ${var.tenant_id} tenant agent"
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"
  role_arn        = aws_iam_role.gateway.arn
}

resource "aws_bedrockagentcore_gateway_target" "documents" {
  name               = local.tool_target_name
  description        = "Read-only documents from the customer's approved S3 folder"
  gateway_identifier = aws_bedrockagentcore_gateway.documents.gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.documents_tool.arn

        tool_schema {
          inline_payload {
            name        = "list_documents"
            description = "List document keys in the customer's approved folder. The folder is fixed by deployment policy; relative_prefix selects a subfolder, not a bucket or account."

            input_schema {
              type = "object"

              property {
                name        = "relative_prefix"
                type        = "string"
                description = "Optional subfolder beneath the approved folder, such as policies/2026. Empty means the approved folder."
              }

              property {
                name        = "limit"
                type        = "integer"
                description = "Maximum number of keys to return, from 1 to 20. Defaults to 10."
              }
            }
          }

          inline_payload {
            name        = "read_document"
            description = "Read up to 32 KiB of a UTF-8 text document from the customer's approved folder. The key is relative to that folder; this tool cannot read other buckets or prefixes."

            input_schema {
              type = "object"

              property {
                name        = "key"
                type        = "string"
                description = "Relative document key returned by list_documents, such as policies/handbook.md."
                required    = true
              }
            }
          }
        }
      }
    }
  }

  depends_on = [aws_iam_role_policy.gateway]
}
