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
  values = { outputs = { documents_bucket_name = "symphony-fixture-666666666666-docs", approved_prefix = "approved-docs/", documents_kms_key_arn = "arn:aws:kms:us-east-1:666666666666:key/33333333-3333-3333-3333-333333333333" } }
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

override_resource {
  target          = aws_bedrockagentcore_code_interpreter.sandbox
  override_during = plan
  values          = { code_interpreter_arn = "arn:aws:bedrock-agentcore:us-east-2:444444444444:code-interpreter-custom/symphony_acme_sandbox-abc" }
}

override_resource {
  target          = aws_bedrockagentcore_browser.web
  override_during = plan
  values          = { browser_arn = "arn:aws:bedrock-agentcore:us-east-2:444444444444:browser-custom/symphony_acme_browser-abc" }
}

override_resource {
  target          = aws_bedrockagentcore_memory.agent
  override_during = plan
  values          = { arn = "arn:aws:bedrock-agentcore:us-east-2:444444444444:memory/symphony_acme_memory-abc", id = "symphony_acme_memory-abc" }
}

override_resource {
  target          = aws_bedrockagent_knowledge_base.documents
  override_during = plan
  values          = { arn = "arn:aws:bedrock:us-east-2:444444444444:knowledge-base/KBABC12345", id = "KBABC12345" }
}

override_resource {
  target          = aws_lambda_function.knowledge_search
  override_during = plan
  values          = { arn = "arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-knowledge-search" }
}

override_resource {
  target          = aws_s3vectors_index.kb
  override_during = plan
  values          = { index_arn = "arn:aws:s3vectors:us-east-2:444444444444:bucket/symphony-acme-kb-444444444444/index/documents" }
}

override_resource {
  target          = aws_cloudwatch_log_group.knowledge_search
  override_during = plan
  values          = { arn = "arn:aws:logs:us-east-2:444444444444:log-group:/symphony/acme/knowledge-search" }
}

run "runs_an_allowlisted_in_region_model" {
  command = plan

  assert {
    condition     = one(one(aws_bedrockagentcore_harness.agent.model).bedrock_model_config).model_id == "moonshotai.kimi-k2.5"
    error_message = "The default private-cloud model is Kimi K2.5 on in-Region Bedrock."
  }

  assert {
    condition = jsondecode(aws_iam_role_policy.harness.policy).Statement[0].Resource == [
      "arn:aws:bedrock:us-east-2::foundation-model/moonshotai.kimi-k2.5",
      "arn:aws:bedrock:us-east-2:444444444444:inference-profile/us.anthropic.claude-opus-4-5-20251101-v1:0",
      "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-4-5-20251101-v1:0",
      "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-opus-4-5-20251101-v1:0",
      "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-opus-4-5-20251101-v1:0",
    ]
    error_message = "The harness may invoke only the default model and the configured US step-up profile."
  }
}

run "allows_only_gateway_tools" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_harness.agent.allowed_tools == tolist(["@docs", "@code", "@web"])
    error_message = "Only gateway, sandbox, and browser tools are allowed; shell and file_operations must stay off."
  }

  assert {
    condition     = toset([for t in aws_bedrockagentcore_harness.agent.tool : "${t.name}:${t.type}"]) == toset(["docs:agentcore_gateway", "code:agentcore_code_interpreter", "web:agentcore_browser"])
    error_message = "Tools are exactly the tenant gateway, the sandbox interpreter, and the allowlisted browser."
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

run "bounds_the_agent" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_harness.agent.max_iterations <= 12 && aws_bedrockagentcore_harness.agent.timeout_seconds <= 300 && aws_bedrockagentcore_harness.agent.max_tokens <= 50000
    error_message = "Iterations, time, and tokens must be bounded."
  }
}

run "gateway_is_iam_only_and_invokes_only_the_tool" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_gateway.documents.authorizer_type == "AWS_IAM" && aws_bedrockagentcore_gateway.documents.protocol_type == "MCP"
    error_message = "The gateway must use IAM inbound auth over MCP."
  }

  assert {
    condition     = contains(jsondecode(aws_iam_role_policy.gateway.policy).Statement[0].Resource, "arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-documents-tool") && length(jsondecode(aws_iam_role_policy.gateway.policy).Statement[0].Resource) == 2
    error_message = "The gateway role may invoke only the two tenant tool Lambdas."
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

# --- phase 3b: execution environments, memory, knowledge base, traces ---
run "code_interpreter_has_no_network" {
  command = plan

  assert {
    condition     = one(aws_bedrockagentcore_code_interpreter.sandbox.network_configuration).network_mode == "SANDBOX"
    error_message = "Code Interpreter must run in SANDBOX mode with no internet."
  }

  assert {
    condition     = one([for s in jsondecode(aws_iam_role_policy.harness.policy).Statement : s.Resource if s.Sid == "SandboxCodeInterpreter"]) == "arn:aws:bedrock-agentcore:us-east-2:444444444444:code-interpreter-custom/symphony_acme_sandbox-abc"
    error_message = "The harness may use only the tenant's sandbox interpreter, not the AWS default."
  }
}

run "browser_is_allowlisted_and_recorded" {
  command = plan

  assert {
    condition     = jsondecode(aws_s3_object.browser_policy.content).URLBlocklist == ["*"] && jsondecode(aws_s3_object.browser_policy.content).URLAllowlist == ["docs.aws.amazon.com", "aws.amazon.com"]
    error_message = "Chrome policy must block every URL except the allowlist."
  }

  assert {
    condition     = one(aws_bedrockagentcore_browser.web.recording).enabled && one(one(aws_bedrockagentcore_browser.web.recording).s3_location).prefix == "recordings/"
    error_message = "Every browser session must be recorded to the tenant bucket."
  }

  assert {
    condition     = one(aws_bedrockagentcore_browser.web.enterprise_policy).type == "MANAGED"
    error_message = "The allowlist must be a managed policy that sessions cannot override."
  }
}

run "memory_is_encrypted_expiring_and_per_user" {
  command = plan

  assert {
    condition     = aws_bedrockagentcore_memory.agent.encryption_key_arn == "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222" && aws_bedrockagentcore_memory.agent.event_expiry_duration == 30
    error_message = "Memory must use the tenant key and expire events after 30 days."
  }

  assert {
    condition     = alltrue([for ns in concat(tolist(aws_bedrockagentcore_memory_strategy.facts.namespace_templates), tolist(aws_bedrockagentcore_memory_strategy.summaries.namespace_templates)) : startswith(ns, "/users/{actorId}/")])
    error_message = "Every memory namespace must be scoped to the actor."
  }

  assert {
    condition     = one(one(aws_bedrockagentcore_harness.agent.memory).agentcore_memory_configuration).arn == "arn:aws:bedrock-agentcore:us-east-2:444444444444:memory/symphony_acme_memory-abc"
    error_message = "The harness must use the tenant memory resource."
  }
}

run "knowledge_base_indexes_only_approved_docs" {
  command = plan

  assert {
    condition     = one(one(aws_bedrockagent_data_source.approved_docs.data_source_configuration).s3_configuration).inclusion_prefixes == toset(["approved-docs/"])
    error_message = "Only approved-docs/ may be ingested; forbidden documents are never indexed."
  }

  assert {
    condition     = aws_s3vectors_index.kb.dimension == 1024 && aws_s3vectors_index.kb.distance_metric == "cosine"
    error_message = "The index must match Titan Embed v2 (1024 dimensions, cosine)."
  }

  assert {
    condition     = aws_s3vectors_vector_bucket.kb.encryption_configuration[0].kms_key_arn == "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222"
    error_message = "Vectors must be encrypted with the tenant key."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.knowledge_search.policy).Statement[0].Resource == "arn:aws:bedrock:us-east-2:444444444444:knowledge-base/KBABC12345"
    error_message = "The search tool may query only the tenant knowledge base."
  }
}

run "traces_stay_encrypted_in_the_tenant" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.spans.retention_in_days == 7 && aws_cloudwatch_log_group.spans.kms_key_id == "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222"
    error_message = "Spans hold prompt payloads: tenant key and 7-day retention."
  }

  assert {
    condition     = aws_xray_trace_segment_destination.this.destination == "CloudWatchLogs" && one(one(aws_xray_indexing_rule.default.rule).probabilistic).desired_sampling_percentage == 1
    error_message = "Transaction Search writes to CloudWatch Logs and indexes 1% by default."
  }
}

run "switches_step_up_to_gpt_with_one_variable" {
  command = plan

  variables {
    step_up_model_id = "us.openai.gpt-6.1-sol"
  }

  assert {
    condition     = contains(jsondecode(aws_iam_role_policy.harness.policy).Statement[0].Resource, "arn:aws:bedrock:us-west-2::foundation-model/openai.gpt-6.1-sol")
    error_message = "Changing step_up_model_id must update the IAM allowlist."
  }
}

run "rejects_a_global_or_unlisted_step_up_profile" {
  command = plan

  variables {
    step_up_model_id = "global.anthropic.claude-opus-5-5"
  }

  expect_failures = [var.step_up_model_id]
}
