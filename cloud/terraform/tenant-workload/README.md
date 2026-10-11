# tenant-workload

The tenant's private-cloud agent (Phase 3 agent slice): an AgentCore Harness running the managed Strands loop on in-Region Bedrock, an IAM-authenticated AgentCore Gateway, and the read-only S3 document tool (`cloud/tools/customer_documents`) as its only target.

| Item | Value |
| --- | --- |
| Account / Region | One tenant account via `AWSControlTowerExecution`; tenant `workload_region` |
| State key | `symphony/tenants/<tenant_id>/workload.tfstate` |
| Apply order | After `tenant-baseline` and `sandbox-fixture`; then apply `external-aws-connector` |
| Inputs | `tenant_id`, `tenant_account_id`, `workload_region`, `state_bucket`, `model_id` (default `moonshotai.kimi-k2.5`), `step_up_model_id` (US cross-Region profile; default Opus 4.5, or `us.anthropic.claude-opus-5-5` / `us.openai.gpt-6.1-sol` once AWS enables them), `browser_allowed_domains`, `trace_indexing_percent` |
| Outputs | `harness_arn`, `gateway_id`, `documents_tool_role_arn`, `external_id`, `model_id`, `step_up_model_id`, `knowledge_base_id`, `knowledge_base_data_source_id`, `knowledge_base_role_arn`, `browser_recordings_bucket`, `memory_id` |

## Controls

- Harness `allowed_tools = ["@docs"]`: only the gateway's two tools. Built-in `shell` and `file_operations` are off, and no role is granted `InvokeAgentRuntimeCommand`.
- Persistent memory disabled; `max_iterations` 8, `timeout_seconds` 120, `max_tokens` 20000.
- Harness role: `bedrock:InvokeModel*` on the one foundation model in the tenant Region, and `InvokeGateway` on the one gateway. No cross-Region inference profile.
- Gateway: `AWS_IAM` inbound auth, MCP; its role may invoke only the document Lambda.
- Lambda: Graviton (`arm64`), 10 s timeout, JSON logs to the KMS-encrypted `/symphony/<tenant_id>/documents-tool` group (30 days). Bucket, prefix, role ARN, and external ID are environment configuration, never model input.

## Phase 3b additions

- **Code Interpreter** `symphony_<tenant>_sandbox`, network mode `SANDBOX` (no internet). Harness tool `code`.
- **Browser** `symphony_<tenant>_browser`: Chrome managed policy blocks every URL except `browser_allowed_domains`; sessions recorded to `symphony-<tenant>-browser-<account>/recordings/` (tenant key, 30-day expiry). Harness tool `web`.
- **Memory** `symphony_<tenant>_memory`: tenant key, 30-day event expiry, semantic and summary strategies under `/users/{actorId}/…`. Pass `--user` (InvokeHarness `actorId`) to isolate users.
- **Knowledge base** on S3 Vectors (tenant key, Titan Embed v2, 1024-d cosine) over the fixture's `approved-docs/` only; `search_documents` on the gateway via `cloud/tools/knowledge_search`.
- **Step-up model** via the InvokeHarness model override (`--model-id`); the harness role may invoke only `model_id` and `step_up_model_id`.
- **Traces**: Transaction Search with `aws/spans` encrypted by the tenant key, 7-day retention. Harness spans include prompt and tool payloads, so they never leave the tenant account.

## Cost-bearing resources

Bedrock tokens (Kimi K2.5; the step-up model is the largest variable cost), AgentCore Runtime, Browser, and Code Interpreter session time, Memory events and records, S3 Vectors storage and queries, Titan embeddings during sync, Gateway and Lambda invocations, and CloudWatch Logs and indexed spans. Nothing runs while idle.

## Try it

```sh
uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/invoke_demo.py \
  --harness-arn "$(terraform -chdir=cloud/terraform/tenant-workload output -raw harness_arn)" \
  --assume-role-arn arn:aws:iam::<tenant-account-id>:role/AWSControlTowerExecution \
  "What does our PTO policy say? Cite the document."
```

The Regional API Gateway (mTLS custom domain), device/user authorizer, and route policy are the next plan; until then operators invoke with SigV4.
