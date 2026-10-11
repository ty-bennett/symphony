# Semantic search over the customer's approved documents. Vectors live in S3
# Vectors in the tenant account under the tenant key; only approved-docs/ is
# ingested, so forbidden documents are never indexed.
locals {
  kb_name          = "symphony-${var.tenant_id}-documents"
  kb_bucket        = "symphony-${var.tenant_id}-kb-${var.tenant_account_id}"
  embedding_model  = "amazon.titan-embed-text-v2:0"
  embedding_arn    = "arn:aws:bedrock:${var.workload_region}::foundation-model/${local.embedding_model}"
  kb_role_name     = "${local.name_prefix}-knowledge-base"
  fixture_docs_arn = "arn:aws:s3:::${local.fixture.documents_bucket_name}"
}

resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = local.kb_bucket
  encryption_configuration = [{
    sse_type    = "aws:kms"
    kms_key_arn = local.tenant_baseline.tenant_data_kms_key_arn
  }]

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "documents"
  data_type          = "float32"
  dimension          = 1024
  distance_metric    = "cosine"

  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }
}

resource "aws_iam_role" "knowledge_base" {
  name = local.kb_role_name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.tenant_account_id } }
    }]
  })

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_role_policy" "knowledge_base" {
  name = "knowledge-base"
  role = aws_iam_role.knowledge_base.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EmbedWithTitan"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.embedding_arn
      },
      {
        Sid      = "VectorIndex"
        Effect   = "Allow"
        Action   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:QueryVectors", "s3vectors:GetIndex"]
        Resource = aws_s3vectors_index.kb.index_arn
      },
      {
        Sid       = "UseTenantKeyViaS3Vectors"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = local.tenant_baseline.tenant_data_kms_key_arn
        Condition = { StringEquals = { "kms:ViaService" = "s3vectors.${var.workload_region}.amazonaws.com" } }
      },
      {
        Sid       = "ListApprovedDocuments"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = local.fixture_docs_arn
        Condition = { StringLike = { "s3:prefix" = [local.fixture.approved_prefix, "${local.fixture.approved_prefix}*"] } }
      },
      {
        Sid      = "ReadApprovedDocuments"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${local.fixture_docs_arn}/${local.fixture.approved_prefix}*"
      },
      {
        Sid       = "DecryptApprovedDocuments"
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = local.fixture.documents_kms_key_arn
        Condition = { StringEquals = { "kms:ViaService" = "s3.us-east-1.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_bedrockagent_knowledge_base" "documents" {
  name     = local.kb_name
  role_arn = aws_iam_role.knowledge_base.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_arn
    }
  }

  storage_configuration {
    type = "S3_VECTORS"

    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  depends_on = [aws_iam_role_policy.knowledge_base]
}

resource "aws_bedrockagent_data_source" "approved_docs" {
  name              = "approved-docs"
  knowledge_base_id = aws_bedrockagent_knowledge_base.documents.id

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn              = local.fixture_docs_arn
      bucket_owner_account_id = local.fixture_account
      inclusion_prefixes      = [local.fixture.approved_prefix]
    }
  }
}

# search_documents tool: Lambda adapter calling Retrieve on this KB only.
data "archive_file" "knowledge_search" {
  type        = "zip"
  source_file = "${path.module}/../../tools/knowledge_search/handler.py"
  output_path = "${path.module}/.build/knowledge_search.zip"
}

resource "aws_cloudwatch_log_group" "knowledge_search" {
  name              = "/symphony/${var.tenant_id}/knowledge-search"
  retention_in_days = 30
  kms_key_id        = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_iam_role" "knowledge_search" {
  name = "${local.name_prefix}-knowledge-search"
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

resource "aws_iam_role_policy" "knowledge_search" {
  name = "knowledge-search"
  role = aws_iam_role.knowledge_search.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RetrieveFromTenantKnowledgeBase"
        Effect   = "Allow"
        Action   = "bedrock:Retrieve"
        Resource = aws_bedrockagent_knowledge_base.documents.arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.knowledge_search.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "knowledge_search" {
  function_name    = "${local.name_prefix}-knowledge-search"
  role             = aws_iam_role.knowledge_search.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.knowledge_search.output_path
  source_code_hash = data.archive_file.knowledge_search.output_base64sha256
  timeout          = 15
  memory_size      = 256

  environment {
    variables = {
      KNOWLEDGE_BASE_ID   = aws_bedrockagent_knowledge_base.documents.id
      EXPECTED_GATEWAY_ID = aws_bedrockagentcore_gateway.documents.gateway_id
      GATEWAY_TARGET_NAME = "knowledge-search"
      SOURCE_PREFIX       = local.fixture.approved_prefix
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.knowledge_search.name
  }

  depends_on = [aws_iam_role_policy.knowledge_search]
}

resource "aws_bedrockagentcore_gateway_target" "knowledge_search" {
  name               = "knowledge-search"
  description        = "Semantic search over the customer's approved documents"
  gateway_identifier = aws_bedrockagentcore_gateway.documents.gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.knowledge_search.arn

        tool_schema {
          inline_payload {
            name        = "search_documents"
            description = "Semantic search over the customer's approved documents. Returns up to 5 short passages with their source document key. Use read_document to read a full document."

            input_schema {
              type = "object"

              property {
                name        = "query"
                type        = "string"
                description = "Natural-language question or keywords."
                required    = true
              }

              property {
                name        = "max_results"
                type        = "integer"
                description = "Number of passages, from 1 to 5. Defaults to 3."
              }
            }
          }
        }
      }
    }
  }

  depends_on = [aws_iam_role_policy.gateway]
}
