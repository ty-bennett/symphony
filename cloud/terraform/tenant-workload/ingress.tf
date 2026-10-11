# Jetson-facing endpoint: https://api.<tenant>.<domain>/v1/requests with mTLS
# (device certificate) + Cognito user token, streaming the agent's answer back
# as server-sent events. The default execute-api hostname is disabled.
locals {
  tenant_zone_name = "${var.tenant_id}.${var.parent_domain}"
  api_hostname     = "api.${local.tenant_zone_name}"
  truststore_key   = "truststore/ca.pem"
  ingress_dist     = "${path.module}/../../services/ingress/dist"
}

# --- DNS: delegated tenant zone + certificate ---------------------------------
resource "aws_route53_zone" "tenant" {
  name    = local.tenant_zone_name
  comment = "Symphony ${var.tenant_id} tenant endpoints"

  depends_on = [terraform_data.account_guard]
}

resource "aws_route53_record" "delegation" {
  provider = aws.management

  zone_id = var.parent_zone_id
  name    = local.tenant_zone_name
  type    = "NS"
  ttl     = 300
  records = aws_route53_zone.tenant.name_servers
}

resource "aws_acm_certificate" "api" {
  domain_name       = local.api_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# One hostname, so one validation record (no for_each over values unknown at plan).
resource "aws_route53_record" "api_validation" {
  zone_id         = aws_route53_zone.tenant.zone_id
  name            = one(aws_acm_certificate.api.domain_validation_options).resource_record_name
  type            = one(aws_acm_certificate.api.domain_validation_options).resource_record_type
  ttl             = 300
  records         = [one(aws_acm_certificate.api.domain_validation_options).resource_record_value]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [aws_route53_record.api_validation.fqdn]

  depends_on = [aws_route53_record.delegation]
}

# --- mTLS truststore -----------------------------------------------------------
module "truststore_bucket" {
  source = "../modules/secure-bucket"

  name        = "symphony-${var.tenant_id}-truststore-${var.tenant_account_id}"
  kms_key_arn = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_s3_object" "truststore" {
  bucket       = module.truststore_bucket.name
  key          = local.truststore_key
  source       = "${path.module}/${var.truststore_pem_path}"
  source_hash  = filemd5("${path.module}/${var.truststore_pem_path}")
  content_type = "application/x-pem-file"
  kms_key_id   = local.tenant_baseline.tenant_data_kms_key_arn
}

# --- Cognito app client for Jetson users ---------------------------------------
resource "aws_cognito_user_pool_client" "jetson" {
  name            = "symphony-${var.tenant_id}-jetson"
  user_pool_id    = local.tenant_baseline.user_pool_id
  generate_secret = false
  # SRP for people on the Jetson; ADMIN_USER_PASSWORD needs IAM credentials and
  # is used only by the operator's stand-in client for live verification.
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30
  enable_token_revocation              = true
  allowed_oauth_flows_user_pool_client = false

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# --- Idempotency ---------------------------------------------------------------
resource "aws_dynamodb_table" "idempotency" {
  name         = "${local.name_prefix}-ingress-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.tenant_baseline.tenant_data_kms_key_arn
  }

  depends_on = [terraform_data.account_guard]
}

# --- Lambdas -------------------------------------------------------------------
data "archive_file" "ingress" {
  type        = "zip"
  source_file = "${local.ingress_dist}/ingress.mjs"
  output_path = "${path.module}/.build/ingress.zip"
}

data "archive_file" "authorizer" {
  type        = "zip"
  source_file = "${local.ingress_dist}/authorizer.mjs"
  output_path = "${path.module}/.build/authorizer.zip"
}

resource "aws_cloudwatch_log_group" "ingress" {
  for_each = toset(["ingress", "authorizer"])

  name              = "/symphony/${var.tenant_id}/${each.key}"
  retention_in_days = 30
  kms_key_id        = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_iam_role" "ingress" {
  for_each = toset(["ingress", "authorizer"])

  name = "${local.name_prefix}-${each.key}"
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

resource "aws_iam_role_policy" "ingress" {
  name = "ingress"
  role = aws_iam_role.ingress["ingress"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeTenantHarness"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeHarness", "bedrock-agentcore:InvokeAgentRuntime"]
        Resource = [aws_bedrockagentcore_harness.agent.arn, "${aws_bedrockagentcore_harness.agent.arn}/*"]
      },
      {
        Sid      = "PrefetchUserMemory"
        Effect   = "Allow"
        Action   = "bedrock-agentcore:RetrieveMemoryRecords"
        Resource = aws_bedrockagentcore_memory.agent.arn
      },
      {
        Sid       = "DescribeMemoryKeyViaAgentCore"
        Effect    = "Allow"
        Action    = "kms:DescribeKey"
        Resource  = local.tenant_baseline.tenant_data_kms_key_arn
        Condition = { StringEquals = { "kms:ViaService" = "bedrock-agentcore.${var.workload_region}.amazonaws.com" } }
      },
      {
        Sid      = "ClaimRequestIds"
        Effect   = "Allow"
        Action   = "dynamodb:PutItem"
        Resource = aws_dynamodb_table.idempotency.arn
      },
      {
        Sid      = "UseTenantKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = local.tenant_baseline.tenant_data_kms_key_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.ingress["ingress"].arn}:*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "authorizer" {
  name = "authorizer"
  role = aws_iam_role.ingress["authorizer"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadDeviceRegistry"
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = local.tenant_baseline.device_registry_table_arn
      },
      {
        Sid      = "DecryptRegistry"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = local.tenant_baseline.tenant_data_kms_key_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.ingress["authorizer"].arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "ingress" {
  function_name    = "${local.name_prefix}-ingress"
  role             = aws_iam_role.ingress["ingress"].arn
  runtime          = "nodejs22.x"
  architectures    = ["arm64"]
  handler          = "ingress.handler"
  filename         = data.archive_file.ingress.output_path
  source_code_hash = data.archive_file.ingress.output_base64sha256
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      TENANT_ID         = var.tenant_id
      HARNESS_ARN       = aws_bedrockagentcore_harness.agent.arn
      MEMORY_ID         = aws_bedrockagentcore_memory.agent.id
      DEFAULT_MODEL_ID  = var.model_id
      STEP_UP_MODEL_ID  = var.step_up_model_id
      IDEMPOTENCY_TABLE = aws_dynamodb_table.idempotency.name
      SYSTEM_PROMPT     = local.harness_system_prompt
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ingress["ingress"].name
  }

  depends_on = [aws_iam_role_policy.ingress]
}

resource "aws_lambda_function" "authorizer" {
  function_name    = "${local.name_prefix}-authorizer"
  role             = aws_iam_role.ingress["authorizer"].arn
  runtime          = "nodejs22.x"
  architectures    = ["arm64"]
  handler          = "authorizer.handler"
  filename         = data.archive_file.authorizer.output_path
  source_code_hash = data.archive_file.authorizer.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      TENANT_ID             = var.tenant_id
      USER_POOL_ID          = local.tenant_baseline.user_pool_id
      APP_CLIENT_ID         = aws_cognito_user_pool_client.jetson.id
      DEVICE_REGISTRY_TABLE = local.tenant_baseline.device_registry_table_name
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ingress["authorizer"].name
  }

  depends_on = [aws_iam_role_policy.authorizer]
}

# --- API Gateway -----------------------------------------------------------------
resource "aws_api_gateway_rest_api" "ingress" {
  name                         = "${local.name_prefix}-ingress"
  description                  = "Jetson ingress for the ${var.tenant_id} agent"
  disable_execute_api_endpoint = true

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_rest_api.ingress.root_resource_id
  path_part   = "v1"
}

resource "aws_api_gateway_resource" "requests" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "requests"
}

resource "aws_api_gateway_authorizer" "device_user" {
  name                             = "device-and-user"
  rest_api_id                      = aws_api_gateway_rest_api.ingress.id
  type                             = "REQUEST"
  authorizer_uri                   = aws_lambda_function.authorizer.invoke_arn
  identity_source                  = "method.request.header.Authorization"
  authorizer_result_ttl_in_seconds = 0
}

resource "aws_api_gateway_method" "post" {
  rest_api_id   = aws_api_gateway_rest_api.ingress.id
  resource_id   = aws_api_gateway_resource.requests.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.device_user.id
}

resource "aws_api_gateway_integration" "post" {
  rest_api_id             = aws_api_gateway_rest_api.ingress.id
  resource_id             = aws_api_gateway_resource.requests.id
  http_method             = aws_api_gateway_method.post.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:${var.workload_region}:lambda:path/2021-11-15/functions/${aws_lambda_function.ingress.arn}/response-streaming-invocations"
  response_transfer_mode  = "STREAM"
  timeout_milliseconds    = 300000
}

resource "aws_api_gateway_gateway_response" "denied" {
  for_each = {
    ACCESS_DENIED = { status = "403", code = "DEVICE_REVOKED" }
    UNAUTHORIZED  = { status = "401", code = "USER_UNAUTHORIZED" }
  }

  rest_api_id   = aws_api_gateway_rest_api.ingress.id
  response_type = each.key
  status_code   = each.value.status
  response_templates = {
    "application/json" = jsonencode({ code = each.value.code, message = "Request was not authorized" })
  }
}

resource "aws_lambda_permission" "api" {
  for_each = {
    ingress    = aws_lambda_function.ingress.function_name
    authorizer = aws_lambda_function.authorizer.function_name
  }

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = each.value
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.ingress.execution_arn}/*"
}

resource "aws_api_gateway_deployment" "ingress" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id

  # Redeploy only when routing, auth, or error responses change (not on computed attributes).
  triggers = {
    redeploy = sha1(jsonencode({
      method      = [aws_api_gateway_method.post.id, aws_api_gateway_method.post.authorization, aws_api_gateway_method.post.authorizer_id]
      integration = [aws_api_gateway_integration.post.uri, aws_api_gateway_integration.post.response_transfer_mode, aws_api_gateway_integration.post.timeout_milliseconds]
      authorizer  = [aws_api_gateway_authorizer.device_user.authorizer_uri, aws_api_gateway_authorizer.device_user.identity_source, aws_api_gateway_authorizer.device_user.authorizer_result_ttl_in_seconds]
      responses   = { for k, r in aws_api_gateway_gateway_response.denied : k => [r.status_code, r.response_templates] }
    }))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id   = aws_api_gateway_rest_api.ingress.id
  deployment_id = aws_api_gateway_deployment.ingress.id
  stage_name    = "live"
}

resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  method_path = "*/*"

  settings {
    metrics_enabled        = true
    throttling_rate_limit  = 20
    throttling_burst_limit = 40
  }
}

resource "aws_api_gateway_domain_name" "api" {
  domain_name              = local.api_hostname
  regional_certificate_arn = aws_acm_certificate_validation.api.certificate_arn
  security_policy          = "TLS_1_2"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  mutual_tls_authentication {
    truststore_uri     = "s3://${module.truststore_bucket.name}/${local.truststore_key}"
    truststore_version = aws_s3_object.truststore.version_id
  }
}

resource "aws_api_gateway_base_path_mapping" "api" {
  api_id      = aws_api_gateway_rest_api.ingress.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  domain_name = aws_api_gateway_domain_name.api.domain_name
}

resource "aws_route53_record" "api" {
  zone_id = aws_route53_zone.tenant.zone_id
  name    = local.api_hostname
  type    = "A"

  alias {
    name                   = aws_api_gateway_domain_name.api.regional_domain_name
    zone_id                = aws_api_gateway_domain_name.api.regional_zone_id
    evaluate_target_health = false
  }
}

# --- WAF rate limit --------------------------------------------------------------
resource "aws_wafv2_web_acl" "ingress" {
  name  = "${local.name_prefix}-ingress"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit-per-ip"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.ingress_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "symphony-${var.tenant_id}-ingress-rate"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "symphony-${var.tenant_id}-ingress"
    sampled_requests_enabled   = false
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_wafv2_web_acl_association" "ingress" {
  resource_arn = aws_api_gateway_stage.v1.arn
  web_acl_arn  = aws_wafv2_web_acl.ingress.arn
}

# --- Alarms (visible in the tenant console; no paging target in the pilot) -----
resource "aws_cloudwatch_metric_alarm" "ingress" {
  for_each = {
    server-errors = { metric = "5XXError", threshold = 5, description = "Ingress returned server errors." }
    denial-spike  = { metric = "4XXError", threshold = 50, description = "Many ingress requests were denied (revoked devices, bad tokens, or policy)." }
  }

  alarm_name          = "${local.name_prefix}-ingress-${each.key}"
  alarm_description   = each.value.description
  namespace           = "AWS/ApiGateway"
  metric_name         = each.value.metric
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiName = aws_api_gateway_rest_api.ingress.name
    Stage   = aws_api_gateway_stage.v1.stage_name
  }
}
