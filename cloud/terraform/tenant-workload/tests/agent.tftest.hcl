mock_provider "aws" {
  override_during = plan
}

mock_provider "archive" {
  override_during = plan
}

mock_provider "random" {
  override_during = plan
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "444444444444" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-2" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = {
    outputs = {
      fixture_account_id = "666666666666"
      tenant_accounts = {
        acme = { account_id = "444444444444", workload_region = "us-east-2" }
      }
    }
  }
}

override_data {
  target = data.terraform_remote_state.tenant_baseline
  values = { outputs = { tenant_data_kms_key_arn = "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222" } }
}

override_data {
  target = data.terraform_remote_state.sandbox_fixture
  values = { outputs = { documents_bucket_name = "symphony-fixture-666666666666-docs", approved_prefix = "approved-docs/" } }
}

override_resource {
  target          = aws_bedrockagentcore_gateway.documents
  override_during = plan
  values = {
    gateway_id  = "symphony-acme-gateway-abc123"
    gateway_arn = "arn:aws:bedrock-agentcore:us-east-2:444444444444:gateway/symphony-acme-gateway-abc123"
  }
}

override_resource {
  target          = aws_lambda_function.documents_tool
  override_during = plan
  values          = { arn = "arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-documents-tool" }
}

override_resource {
  target          = aws_cloudwatch_log_group.documents_tool
  override_during = plan
  values          = { arn = "arn:aws:logs:us-east-2:444444444444:log-group:/symphony/acme/documents-tool" }
}

run "runs_an_allowlisted_in_region_model" {
  command = plan

  assert {
    condition     = one(one(aws_bedrockagentcore_harness.agent.model).bedrock_model_config).model_id == "moonshotai.kimi-k2.5"
    error_message = "The default private-cloud model is Kimi K2.5 on in-Region Bedrock."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.harness.policy).Statement[0].Resource == ["arn:aws:bedrock:us-east-2::foundation-model/moonshotai.kimi-k2.5"]
    error_message = "The harness may invoke only the configured foundation model in the tenant Region."
  }
}

run "allows_only_gateway_tools" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_harness.agent.allowed_tools == tolist(["@docs"])
    error_message = "Only the docs gateway tools are allowed; shell and file_operations must stay off."
  }

  assert {
    condition     = one(aws_bedrockagentcore_harness.agent.tool).type == "agentcore_gateway"
    error_message = "The only tool is the tenant AgentCore Gateway."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.harness.policy, "InvokeAgentRuntimeCommand")
    error_message = "The harness role must not allow direct runtime commands."
  }

  assert {
    condition     = contains([for s in jsondecode(aws_iam_role_policy.harness.policy).Statement : s.Resource if s.Sid == "InvokeTenantGateway"], "arn:aws:bedrock-agentcore:us-east-2:444444444444:gateway/symphony-acme-gateway-abc123")
    error_message = "The harness may invoke only the tenant gateway."
  }
}

run "bounds_the_agent_and_disables_memory" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_harness.agent.max_iterations <= 10 && aws_bedrockagentcore_harness.agent.timeout_seconds <= 300 && aws_bedrockagentcore_harness.agent.max_tokens <= 50000
    error_message = "Iterations, time, and tokens must be bounded."
  }

  assert {
    condition     = length(one(aws_bedrockagentcore_harness.agent.memory).disabled) == 1
    error_message = "Persistent memory is disabled for the stateless pilot."
  }
}

run "gateway_is_iam_only_and_invokes_only_the_tool" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_gateway.documents.authorizer_type == "AWS_IAM" && aws_bedrockagentcore_gateway.documents.protocol_type == "MCP"
    error_message = "The gateway must use IAM inbound auth over MCP."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.gateway.policy).Statement[0].Resource == "arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-documents-tool"
    error_message = "The gateway role may invoke only the document tool."
  }

  assert {
    condition     = toset([for t in one(one(one(aws_bedrockagentcore_gateway_target.documents.target_configuration).mcp).lambda).tool_schema[0].inline_payload : t.name]) == toset(["list_documents", "read_document"])
    error_message = "The target exposes exactly the two read-only document tools."
  }
}

run "pins_tool_configuration_outside_the_model" {
  command = plan

  assert {
    condition     = aws_lambda_function.documents_tool.architectures == tolist(["arm64"]) && aws_lambda_function.documents_tool.timeout <= 15
    error_message = "The tool runs on Graviton with a short timeout."
  }

  assert {
    condition     = one(aws_lambda_function.documents_tool.environment).variables["DOCS_BUCKET"] == "symphony-fixture-666666666666-docs" && one(aws_lambda_function.documents_tool.environment).variables["DOCS_PREFIX"] == "approved-docs/"
    error_message = "Bucket and prefix come from deployment state, never from the model."
  }

  assert {
    condition     = one(aws_lambda_function.documents_tool.environment).variables["EXTERNAL_ROLE_ARN"] == "arn:aws:iam::666666666666:role/SymphonyIntegrationRole"
    error_message = "The tool assumes only the fixture account's integration role."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.documents_tool.policy).Statement[0].Resource == "arn:aws:iam::666666666666:role/SymphonyIntegrationRole"
    error_message = "The tool role may assume only the integration role."
  }
}

run "rejects_a_model_outside_the_allowlist" {
  command = plan

  variables {
    model_id = "anthropic.claude-opus-4-5"
  }

  expect_failures = [var.model_id]
}
