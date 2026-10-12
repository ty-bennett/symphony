# Agent tools, memory, and models (Phase 3b) — design

Status: approved in conversation 2026-10-11; awaiting written-spec review.
Builds on: `cloud/PLAN.md` (Phase 3), the deployed agent slice (`cloud/terraform/tenant-workload`, `external-aws-connector`), and `cloud/evidence/phase-3.md`.

## Intent

**Outcome.** The tenant agent in `demo` (`603238661867`, us-east-1) can analyze data in a sandbox, browse allowlisted websites, remember a user across sessions, search the customer's documents semantically, and step up to Claude Opus 5.5 for hard tasks — while the model layer retains nothing and every request is traceable to tenant, user, agent, tool, and model.

**Who it is for.** The Palmetto Hacks demo audience and the first pilot customer: it must show capability *and* the security story ("your data never leaves your account, is never trained on, and is never kept by the model layer").

**What the user said.** Execution environments = AgentCore Code Interpreter + Browser. Demo goals: sandboxed data analysis, safe browsing, agent identity + audit. Memory: AgentCore Memory **and** an S3 Vectors knowledge base. Models: cross-Region inference is acceptable; deploy **Claude Opus 5.5**; the user submits the Anthropic use-case form. Zero data retention (ZDR) in tenant accounts, enforced by SCP.

**Assumptions (correct me).** Single tenant (`demo`) for now; per-tenant design so a second tenant is configuration only. Operators invoke via SigV4 (`invoke_demo.py`); the mTLS/Cognito ingress is a later plan. Kimi K2.5 stays the default model.

**Success criteria (live, in the demo tenant).**
1. "Total Q3 revenue by region from the sales CSV" → the agent reads the CSV via the documents tool, computes in Code Interpreter, and answers with numbers; Code Interpreter has no internet.
2. "Summarize the AgentCore Browser docs page" → visits `docs.aws.amazon.com`; a request to a non-allowlisted site is blocked by the browser; the session recording lands in the tenant's encrypted bucket.
3. Session A: "My name is Ty and I prefer tables." Later session B (same user): the agent recalls the preference. The same question as a different user: no recall.
4. "Which policy mentions notice periods?" → answered via semantic search over `approved-docs/`; the forbidden canary is never returned (it is never indexed).
5. `--model opus` routes one request to Opus 5.5 through the `us.` profile and returns an answer.
6. Bedrock data retention reads `none` in us-east-1, us-east-2, and us-west-2 for the tenant account; an attempt to set any other mode is denied by SCP.
7. A trace shows harness → model → tool spans with tenant, user (actor), agent, tool, and model attributes and **no prompt or document text**.

## Data ownership

| Account | Customer data? |
| --- | --- |
| Management, Audit, Log Archive, Shared Services (Symphony) | No content; security metadata only (CloudTrail, findings, budgets). |
| Tenant `603238661867` (dedicated to one customer) | Only that customer's: memory events and records, KB vectors, browser recordings, traces — all under the tenant KMS key with expiry. |
| Customer's existing account (fixture `719453899809`) | Original documents; never copied except as KB embeddings. |
| Bedrock model layer | Nothing (ZDR). |

## Components

All new resources live in the `tenant-workload` root unless noted, follow the existing guard / `jsonencode` / `prevent_destroy` conventions, and are covered by plan-mode `terraform test` with mocked providers.

### 1. Models and ZDR

- **Default:** `moonshotai.kimi-k2.5`, in-Region on-demand (unchanged).
- **Step-up:** `us.anthropic.claude-opus-5-5` (cross-Region profile: us-east-1, us-east-2, us-west-2). Selected per request by the caller via the `InvokeHarness` `model` override; the harness IAM role allowlist (not the caller) is the boundary: `bedrock:InvokeModel*` on the Kimi foundation model, the `us.` Opus 5.5 inference profile, and the three Regional Opus 5.5 foundation-model ARNs.
- **Anthropic access:** the user submits the use-case form in the tenant account console (AWSAdministratorAccess already permits it). No Terraform resource; the plan verifies with a Converse call.
- **SCP change (organization root):** the region guardrail keeps denying everything outside us-east-1/us-east-2, except Bedrock inference actions (`bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`, `bedrock:ConverseStream`), which are additionally allowed in us-west-2 — US only, required for the `us.` profile.
- **ZDR:** set `data_retention_mode = none` for the tenant account in us-east-1, us-east-2, and us-west-2 (Bedrock control plane `PUT /data-retention`; there is no CLI command or Terraform resource yet, so a small, idempotent, unit-tested script runs during deploy and the verification step re-checks it). **Enforcement:** an SCP on the Customers OU denies `bedrock:PutAccountDataRetention`, `bedrock-mantle:PutAccountDataRetention`, `bedrock-mantle:CreateProject`, and `bedrock-mantle:UpdateProject` unless the requested `DataRetentionMode` is `none`. The script runs as `AWSControlTowerExecution`, which is *not* exempt from this statement, so it can only ever set `none`.
- **Conflict rule:** if Opus 5.5 (or Kimi K2.5) does not accept `none`, ZDR wins: that model is unavailable, the plan records it, and the user chooses between Opus 4.8 (documented to accept `none`) or relaxing ZDR for that model. No silent fallback to a retaining mode.

### 2. Code Interpreter (execution environment)

- `aws_bedrockagentcore_code_interpreter` named `symphony-<tenant>-sandbox`, `network_mode = SANDBOX` (no internet, no VPC).
- Harness tool of type `agentcore_code_interpreter`, name `code`, pointing at the custom ARN; `allowed_tools` adds the matching pattern (exact pattern confirmed against the harness tool docs during planning).
- Harness role adds `StartCodeInterpreterSession`, `StopCodeInterpreterSession`, `GetCodeInterpreterSession`, `ListCodeInterpreterSessions`, and `InvokeCodeInterpreter` on the custom ARN only (not the AWS default interpreter).
- Data path: the agent reads a CSV with `read_document` and passes its contents into the sandbox; the sandbox cannot reach S3 or the internet itself.
- The fixture gets `approved-docs/sales-2026-q3.csv` (synthetic, about 60 rows of region × product × revenue), within the 32 KiB read cap.

### 3. Browser (execution environment)

- `aws_bedrockagentcore_browser` named `symphony-<tenant>-browser`, `network_mode = PUBLIC`, **recording enabled** to `s3://symphony-<tenant>-browser-recordings-<account>/` (secure-bucket module, tenant KMS key, 30-day expiry).
- **Allowlist:** a Chrome enterprise policy JSON stored in that bucket (`URLBlocklist = ["*"]`, `URLAllowlist = var.browser_allowed_domains`, default `["docs.aws.amazon.com", "aws.amazon.com"]`) attached through `enterprise_policy`. The plan confirms the policy document format against the AgentCore Browser docs.
- Browser execution role: write recordings to its prefix, read the policy object, use the tenant key. Harness role: browser session actions on the custom browser ARN only.
- Harness tool of type `agentcore_browser`, name `web`; `allowed_tools` adds its pattern.

### 4. AgentCore Memory

- `aws_bedrockagentcore_memory` named `symphony_<tenant>_memory`: `encryption_key_arn` = tenant data key, `event_expiry_duration = 30` (days), and a memory execution role for extraction model calls, scoped to the extraction model.
- Strategies: **semantic** facts at namespace `/users/{actorId}/facts` and **summary** at `/users/{actorId}/sessions/{sessionId}`.
- The harness `memory` block switches from `disabled` to `agentcore_memory_configuration` with this memory's ARN; each request's actor ID is the authenticated user (demo client `--user`). **Isolation requirement:** retrieval is scoped to the caller's actor namespace. The plan must confirm `InvokeHarness` accepts a per-request actor; if it does not, the plan stops and reports — tenant-wide memory is not acceptable.
- Harness role adds `CreateEvent`, `GetEvent`, `ListEvents`, `DeleteEvent`, and `RetrieveMemoryRecords` on this memory ARN only.

### 5. Knowledge base on S3 Vectors

- `aws_s3vectors_vector_bucket` (tenant KMS key) and `aws_s3vectors_index` (dimension 1024, cosine, float32) in the tenant account.
- `aws_bedrockagent_knowledge_base` with `s3_vectors_configuration`, embedding model `amazon.titan-embed-text-v2:0` (in-Region), and a KB role scoped to the index, the embedding model, and the source bucket.
- Data source: the fixture bucket with **inclusion prefix `approved-docs/`**, so forbidden documents are never indexed. Cross-account read: the customer side (`external-aws-connector`) adds a bucket-policy statement and KMS permission for the KB role only.
- Ingestion: a start-and-wait sync script after apply, safe to re-run.
- Agent access: a Gateway target exposing `search_documents(query, max_results ≤ 5)`, using the managed Knowledge Bases connector if it can be pinned to this KB ID; otherwise a small Lambda adapter (same pattern and tests as the documents tool) that calls `Retrieve` on the fixed KB ID. The plan picks after checking the connector's configuration schema.

### 6. Agent identity and audit

- Each harness has its AgentCore workload identity (`harness_<name>-*`); traces carry it as the agent ID.
- Enable CloudWatch Transaction Search in the tenant account: `aws_xray_trace_segment_destination` set to CloudWatch Logs, the required logs resource policy, and span indexing. AgentCore emits harness, model, and tool spans to the `aws/spans` log group.
- **No prompt text:** the harness environment disables GenAI message-content capture (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`, or the documented equivalent). Verification sends a prompt containing a unique canary phrase and searches recent spans for it; it must find none.
- The demo client prints the session ID and a CloudWatch trace link; a runbook query lists tenant, actor, agent, tool, model, latency, and tokens for a session.

## Error handling

- Model unavailable (form pending, ZDR conflict): `invoke_demo.py` surfaces the Bedrock error code; no automatic fallback to another model.
- Browser blocked URL: returned to the agent as a tool error; the agent says it cannot access that site.
- Code Interpreter or tool timeouts: bounded by the harness `timeout_seconds` (raised to 300 for analysis tasks) and `max_iterations` (raised to 12).
- KB sync failure: the sync script exits non-zero with the job's failure reasons; the agent's other tools keep working.
- Memory unavailable: the invocation fails visibly; no silent stateless fallback that would confuse recall demos.

## Testing

- **Offline (CI):** `terraform test` for every new resource and policy — harness tool list and allowlist, IAM resources pinned to specific ARNs, Code Interpreter `SANDBOX`, browser recording and policy, memory CMK / expiry / namespaces, S3 Vectors encryption and dimension, KB inclusion prefix, SCP statements (us-west-2 Bedrock-only exception, ZDR deny), and that prompt capture is not enabled. Python unit tests for the ZDR script, the KB sync script, and any KB adapter Lambda.
- **Live (`cloud/evidence/phase-3.md`):** success criteria 1–7, each scripted in `cloud/services/agent/verify_phase3b.py`, plus re-running the phase 3 denial tests. Every root re-plans with exit 0.

## Out of scope

mTLS/Cognito ingress and device enrollment (3.1–3.4); per-user *document* authorization (3.17); the model A/B benchmark (3.20–3.22); Jetson; Snowflake; multi-tenant onboarding automation.

## Cost notes

Opus 5.5 is the dominant variable cost (per token, on demand only). S3 Vectors and Memory are pay-per-use at tiny volumes. Browser and Code Interpreter bill per session time. Transaction Search bills on indexed spans: keep indexing at the default 1%, raise it to 100% only for the demo window, then lower it again.
