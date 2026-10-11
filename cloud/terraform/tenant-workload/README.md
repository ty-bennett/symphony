# tenant-workload

The tenant's private-cloud agent (Phase 3 agent slice): an AgentCore Harness running the managed Strands loop on in-Region Bedrock, an IAM-authenticated AgentCore Gateway, and the read-only S3 document tool (`cloud/tools/customer_documents`) as its only target.

| Item | Value |
| --- | --- |
| Account / Region | One tenant account via `AWSControlTowerExecution`; tenant `workload_region` |
| State key | `symphony/tenants/<tenant_id>/workload.tfstate` |
| Apply order | After `tenant-baseline` and `sandbox-fixture`; then apply `external-aws-connector` |
| Inputs | `tenant_id`, `tenant_account_id`, `workload_region`, `state_bucket`, `model_id` (default `moonshotai.kimi-k2.5`; allowlisted: `zai.glm-5`, `qwen.qwen3-32b-v1:0`) |
| Outputs | `harness_arn`, `gateway_id`, `documents_tool_role_arn`, `external_id`, `model_id` |

## Controls

- Harness `allowed_tools = ["@docs"]`: only the gateway's two tools. Built-in `shell` and `file_operations` are off, and no role is granted `InvokeAgentRuntimeCommand`.
- Persistent memory disabled; `max_iterations` 8, `timeout_seconds` 120, `max_tokens` 20000.
- Harness role: `bedrock:InvokeModel*` on the one foundation model in the tenant Region, and `InvokeGateway` on the one gateway. No cross-Region inference profile.
- Gateway: `AWS_IAM` inbound auth, MCP; its role may invoke only the document Lambda.
- Lambda: Graviton (`arm64`), 10 s timeout, JSON logs to the KMS-encrypted `/symphony/<tenant_id>/documents-tool` group (30 days). Bucket, prefix, role ARN, and external ID are environment configuration, never model input.

## Cost-bearing resources

Bedrock on-demand tokens (Kimi K2.5), AgentCore Runtime session time for the harness, Gateway invocations, Lambda invocations, and CloudWatch Logs. Nothing runs while idle.

## Try it

```sh
uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/invoke_demo.py \
  --harness-arn "$(terraform -chdir=cloud/terraform/tenant-workload output -raw harness_arn)" \
  --assume-role-arn arn:aws:iam::<tenant-account-id>:role/AWSControlTowerExecution \
  "What does our PTO policy say? Cite the document."
```

The Regional API Gateway (mTLS custom domain), device/user authorizer, and route policy are the next plan; until then operators invoke with SigV4.
