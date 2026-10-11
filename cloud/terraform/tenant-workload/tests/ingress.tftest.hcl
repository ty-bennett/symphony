mock_provider "aws" {
  override_during = plan
}

mock_provider "aws" {
  alias = "management"
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
  values = {
    outputs = {
      tenant_data_kms_key_arn    = "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222"
      user_pool_id               = "us-east-2_AcmePool1"
      device_registry_table_name = "symphony-acme-device-registry"
      device_registry_table_arn  = "arn:aws:dynamodb:us-east-2:444444444444:table/symphony-acme-device-registry"
    }
  }
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

override_resource {
  target          = aws_lambda_function.ingress
  override_during = plan
  values = {
    arn        = "arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-ingress"
    invoke_arn = "arn:aws:apigateway:us-east-2:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-ingress/invocations"
  }
}

override_resource {
  target          = aws_route53_zone.tenant
  override_during = plan
  values = {
    zone_id      = "Z0TENANTACME0"
    name_servers = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.net"]
  }
}

override_resource {
  target          = aws_acm_certificate.api
  override_during = plan
  values = {
    arn = "arn:aws:acm:us-east-2:444444444444:certificate/44444444-4444-4444-4444-444444444444"
    domain_validation_options = [{
      domain_name           = "api.acme.symphony-edge.link"
      resource_record_name  = "_abc.api.acme.symphony-edge.link."
      resource_record_type  = "CNAME"
      resource_record_value = "_def.acm-validations.aws."
    }]
  }
}

override_resource {
  target          = aws_s3_object.truststore
  override_during = plan
  values          = { version_id = "v1-truststore" }
}

override_resource {
  target          = aws_bedrockagentcore_harness.agent
  override_during = plan
  values          = { arn = "arn:aws:bedrock-agentcore:us-east-2:444444444444:harness/symphony_acme_agent-abc" }
}

override_resource {
  target          = aws_dynamodb_table.idempotency
  override_during = plan
  values          = { arn = "arn:aws:dynamodb:us-east-2:444444444444:table/symphony-acme-ingress-requests" }
}

override_resource {
  target          = aws_cloudwatch_log_group.ingress
  override_during = plan
  values          = { arn = "arn:aws:logs:us-east-2:444444444444:log-group:/symphony/acme/ingress" }
}

run "endpoint_lives_under_the_tenant_zone" {
  command = plan

  assert {
    condition     = aws_route53_zone.tenant.name == "acme.symphony-edge.link"
    error_message = "Each tenant gets its own delegated zone under the parent domain."
  }

  assert {
    condition     = aws_route53_record.delegation.zone_id == "Z0TESTPARENT0" && aws_route53_record.delegation.type == "NS" && aws_route53_record.delegation.records == toset(["ns-1.awsdns-01.org", "ns-2.awsdns-02.net"])
    error_message = "The parent zone (management account) delegates the tenant zone by NS records."
  }

  assert {
    condition     = aws_acm_certificate.api.domain_name == "api.acme.symphony-edge.link" && aws_acm_certificate.api.validation_method == "DNS"
    error_message = "The API certificate covers only api.<tenant>.<domain> and is DNS-validated."
  }

  assert {
    condition     = aws_route53_record.api_validation.zone_id == "Z0TENANTACME0" && aws_route53_record.api_validation.name == "_abc.api.acme.symphony-edge.link."
    error_message = "Certificate validation records live in the tenant zone."
  }
}

run "only_the_mtls_custom_domain_answers" {
  command = plan

  assert {
    condition     = aws_api_gateway_rest_api.ingress.disable_execute_api_endpoint == true
    error_message = "The default execute-api hostname must be disabled."
  }

  assert {
    condition     = one(aws_api_gateway_domain_name.api.mutual_tls_authentication).truststore_uri == "s3://symphony-acme-truststore-444444444444/truststore/ca.pem"
    error_message = "The custom domain must require client certificates from the tenant truststore."
  }

  assert {
    condition     = one(aws_api_gateway_domain_name.api.mutual_tls_authentication).truststore_version == "v1-truststore"
    error_message = "The domain pins the truststore object version so a CA change is an explicit deploy."
  }

  assert {
    condition     = aws_api_gateway_domain_name.api.security_policy == "TLS_1_2" && one(aws_api_gateway_domain_name.api.endpoint_configuration).types == tolist(["REGIONAL"])
    error_message = "The domain is Regional with TLS 1.2 or later."
  }

  assert {
    condition     = aws_route53_record.api.name == "api.acme.symphony-edge.link" && aws_route53_record.api.type == "A"
    error_message = "api.<tenant>.<domain> aliases the custom domain."
  }
}

run "streams_from_lambda_behind_the_authorizer" {
  command = plan

  assert {
    condition     = aws_api_gateway_integration.post.type == "AWS_PROXY" && aws_api_gateway_integration.post.response_transfer_mode == "STREAM"
    error_message = "The integration must stream the Lambda response."
  }

  assert {
    condition     = aws_api_gateway_integration.post.uri == "arn:aws:apigateway:us-east-2:lambda:path/2021-11-15/functions/arn:aws:lambda:us-east-2:444444444444:function:symphony-acme-ingress/response-streaming-invocations"
    error_message = "Streaming requires the response-streaming-invocations Lambda path."
  }

  assert {
    condition     = aws_api_gateway_method.post.http_method == "POST" && aws_api_gateway_method.post.authorization == "CUSTOM"
    error_message = "POST /v1/requests requires the device-and-user authorizer."
  }

  assert {
    condition     = aws_api_gateway_authorizer.device_user.type == "REQUEST" && aws_api_gateway_authorizer.device_user.authorizer_result_ttl_in_seconds == 0
    error_message = "The authorizer is uncached so a revoked device is denied on its next request."
  }

  assert {
    condition     = aws_api_gateway_gateway_response.denied["ACCESS_DENIED"].status_code == "403" && strcontains(aws_api_gateway_gateway_response.denied["ACCESS_DENIED"].response_templates["application/json"], "DEVICE_REVOKED")
    error_message = "An authorizer deny is reported as 403 DEVICE_REVOKED."
  }

  assert {
    condition     = aws_api_gateway_gateway_response.denied["UNAUTHORIZED"].status_code == "401" && strcontains(aws_api_gateway_gateway_response.denied["UNAUTHORIZED"].response_templates["application/json"], "USER_UNAUTHORIZED")
    error_message = "A missing or invalid user token is reported as 401 USER_UNAUTHORIZED."
  }
}

run "throttles_and_rate_limits" {
  command = plan

  assert {
    condition     = one(aws_api_gateway_method_settings.all.settings).throttling_rate_limit == 20 && one(aws_api_gateway_method_settings.all.settings).throttling_burst_limit == 40
    error_message = "The stage is throttled."
  }

  assert {
    condition     = one(one(one(aws_wafv2_web_acl.ingress.rule).statement).rate_based_statement).limit == 500
    error_message = "WAF blocks a source IP above the rate limit."
  }
}

run "lambdas_have_only_what_they_need" {
  command = plan

  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.ingress.policy).Statement : s.Sid] == [
      "InvokeTenantHarness", "PrefetchUserMemory", "DescribeMemoryKeyViaAgentCore", "ClaimRequestIds", "UseTenantKey", "WriteOwnLogs",
    ]
    error_message = "The ingress role may invoke the harness, read memory, claim request IDs, and log; nothing else."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.ingress.policy).Statement[1].Action == "bedrock-agentcore:RetrieveMemoryRecords"
    error_message = "Memory prefetch is read-only."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.ingress.policy).Statement[2].Condition.StringEquals["kms:ViaService"] == "bedrock-agentcore.us-east-2.amazonaws.com"
    error_message = "AgentCore Memory describes the tenant key on the caller's behalf, and only through AgentCore."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.ingress.policy).Statement[3].Action == "dynamodb:PutItem"
    error_message = "Idempotency needs only a conditional put."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.authorizer.policy).Statement[0].Action == "dynamodb:GetItem" && jsondecode(aws_iam_role_policy.authorizer.policy).Statement[0].Resource == "arn:aws:dynamodb:us-east-2:444444444444:table/symphony-acme-device-registry"
    error_message = "The authorizer reads only the device registry."
  }

  assert {
    condition     = aws_lambda_function.ingress.environment[0].variables.TENANT_ID == "acme" && !contains(keys(aws_lambda_function.ingress.environment[0].variables), "PROMPT")
    error_message = "The tenant comes from configuration, never from the request."
  }

  assert {
    condition     = aws_lambda_function.authorizer.environment[0].variables.USER_POOL_ID == "us-east-2_AcmePool1"
    error_message = "The authorizer accepts tokens only from the tenant pool."
  }
}

run "jetson_client_has_no_secret_and_short_tokens" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool_client.jetson.generate_secret == false && aws_cognito_user_pool_client.jetson.access_token_validity == 60
    error_message = "Jetson is a public client with 60-minute access tokens."
  }
}

run "idempotency_keys_expire_and_are_encrypted" {
  command = plan

  assert {
    condition     = one(aws_dynamodb_table.idempotency.ttl).enabled == true && one(aws_dynamodb_table.idempotency.ttl).attribute_name == "expires_at"
    error_message = "Request IDs expire after their 24-hour window."
  }

  assert {
    condition     = one(aws_dynamodb_table.idempotency.server_side_encryption).kms_key_arn == "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222"
    error_message = "The idempotency table uses the tenant key."
  }
}

run "alarms_on_server_errors_and_denial_spikes" {
  command = plan

  assert {
    condition     = aws_cloudwatch_metric_alarm.ingress["server-errors"].metric_name == "5XXError" && aws_cloudwatch_metric_alarm.ingress["server-errors"].dimensions.ApiName == "symphony-acme-ingress"
    error_message = "Alarm on API 5xx responses."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.ingress["denial-spike"].metric_name == "4XXError" && aws_cloudwatch_metric_alarm.ingress["denial-spike"].threshold == 50
    error_message = "Alarm on a spike of denials (4xx)."
  }
}

run "ingress_sends_the_same_system_prompt_as_the_harness" {
  command = plan

  assert {
    condition     = aws_lambda_function.ingress.environment[0].variables.SYSTEM_PROMPT == one(aws_bedrockagentcore_harness.agent.system_prompt).text
    error_message = "The per-request prompt (base + memory facts) must start from the harness prompt."
  }
}
