# Orchestra for the Jetson gateway: a model endpoint that leaves tools to OpenCode
# (POST /v1/chat/completions) and owner-only team management backed by Cognito
# (/v1/admin/users). Both sit behind the same mTLS domain and device-and-user authorizer.
locals {
  user_pool_arn = "arn:aws:cognito-idp:${var.workload_region}:${var.tenant_account_id}:userpool/${local.tenant_baseline.user_pool_id}"
}

# --- Roles as Cognito groups (no group means member) -------------------------
resource "aws_cognito_user_group" "role" {
  for_each = {
    owner  = "Runs the business's Symphony: settings, spending, team, coding agent."
    member = "Starts jobs and approves their own."
  }

  name         = each.key
  description  = each.value
  user_pool_id = local.tenant_baseline.user_pool_id
}

# --- Lambdas -----------------------------------------------------------------
data "archive_file" "orchestra" {
  for_each = toset(["chat-lambda", "admin-lambda"])

  type        = "zip"
  source_file = "${local.ingress_dist}/${each.key}.mjs"
  output_path = "${path.module}/.build/${each.key}.zip"
}

resource "aws_cloudwatch_log_group" "orchestra" {
  for_each = toset(["chat", "admin"])

  name              = "/symphony/${var.tenant_id}/${each.key}"
  retention_in_days = 30
  kms_key_id        = local.tenant_baseline.tenant_data_kms_key_arn
}

resource "aws_iam_role" "orchestra" {
  for_each = toset(["chat", "admin"])

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

resource "aws_iam_role_policy" "chat" {
  name = "chat"
  role = aws_iam_role.orchestra["chat"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeAllowlistedModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = local.allowed_model_arns
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.orchestra["chat"].arn}:*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "admin" {
  name = "admin"
  role = aws_iam_role.orchestra["admin"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageTeamInTenantPool"
        Effect = "Allow"
        Action = [
          "cognito-idp:ListUsers",
          "cognito-idp:ListUsersInGroup",
          "cognito-idp:AdminCreateUser",
          "cognito-idp:AdminAddUserToGroup",
          "cognito-idp:AdminDisableUser",
          "cognito-idp:AdminUserGlobalSignOut",
        ]
        Resource = local.user_pool_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.orchestra["admin"].arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "chat" {
  function_name    = "${local.name_prefix}-chat"
  role             = aws_iam_role.orchestra["chat"].arn
  runtime          = "nodejs22.x"
  architectures    = ["arm64"]
  handler          = "chat-lambda.handler"
  filename         = data.archive_file.orchestra["chat-lambda"].output_path
  source_code_hash = data.archive_file.orchestra["chat-lambda"].output_base64sha256
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      TENANT_ID        = var.tenant_id
      DEFAULT_MODEL_ID = var.model_id
      STEP_UP_MODEL_ID = var.step_up_model_id
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.orchestra["chat"].name
  }

  depends_on = [aws_iam_role_policy.chat]
}

resource "aws_lambda_function" "admin" {
  function_name    = "${local.name_prefix}-admin"
  role             = aws_iam_role.orchestra["admin"].arn
  runtime          = "nodejs22.x"
  architectures    = ["arm64"]
  handler          = "admin-lambda.handler"
  filename         = data.archive_file.orchestra["admin-lambda"].output_path
  source_code_hash = data.archive_file.orchestra["admin-lambda"].output_base64sha256
  timeout          = 15
  memory_size      = 256

  environment {
    variables = {
      TENANT_ID    = var.tenant_id
      USER_POOL_ID = local.tenant_baseline.user_pool_id
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.orchestra["admin"].name
  }

  depends_on = [aws_iam_role_policy.admin]
}

resource "aws_lambda_permission" "orchestra" {
  for_each = {
    chat  = aws_lambda_function.chat.function_name
    admin = aws_lambda_function.admin.function_name
  }

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = each.value
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.ingress.execution_arn}/*"
}

# --- Routes ------------------------------------------------------------------
resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "chat"
}

resource "aws_api_gateway_resource" "chat_completions" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.chat.id
  path_part   = "completions"
}

resource "aws_api_gateway_method" "chat" {
  rest_api_id   = aws_api_gateway_rest_api.ingress.id
  resource_id   = aws_api_gateway_resource.chat_completions.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.device_user.id
}

resource "aws_api_gateway_integration" "chat" {
  rest_api_id             = aws_api_gateway_rest_api.ingress.id
  resource_id             = aws_api_gateway_resource.chat_completions.id
  http_method             = aws_api_gateway_method.chat.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:${var.workload_region}:lambda:path/2021-11-15/functions/${aws_lambda_function.chat.arn}/response-streaming-invocations"
  response_transfer_mode  = "STREAM"
  timeout_milliseconds    = 300000
}

resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "admin"
}

resource "aws_api_gateway_resource" "admin_users" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "users"
}

resource "aws_api_gateway_resource" "admin_user" {
  rest_api_id = aws_api_gateway_rest_api.ingress.id
  parent_id   = aws_api_gateway_resource.admin_users.id
  path_part   = "{id}"
}

locals {
  admin_methods = {
    list   = { resource = aws_api_gateway_resource.admin_users.id, method = "GET" }
    invite = { resource = aws_api_gateway_resource.admin_users.id, method = "POST" }
    remove = { resource = aws_api_gateway_resource.admin_user.id, method = "DELETE" }
  }
}

resource "aws_api_gateway_method" "admin" {
  for_each = local.admin_methods

  rest_api_id   = aws_api_gateway_rest_api.ingress.id
  resource_id   = each.value.resource
  http_method   = each.value.method
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.device_user.id
}

resource "aws_api_gateway_integration" "admin" {
  for_each = local.admin_methods

  rest_api_id             = aws_api_gateway_rest_api.ingress.id
  resource_id             = each.value.resource
  http_method             = aws_api_gateway_method.admin[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.admin.invoke_arn
}
