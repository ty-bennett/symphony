# Phase 3 evidence (agent slice)

Synthetic data only. Account IDs are deployment records, not secrets.

| Item | Date | Account / Region | Command or test | Result |
| --- | --- | --- | --- | --- |
| 3.5 Harness deployed | 2026-10-11 | demo 603238661867 / us-east-1 | `terraform -chdir=cloud/terraform/tenant-workload apply` | PASS: `symphony_demo_agent` on `moonshotai.kimi-k2.5`, memory disabled, max_iterations 8, timeout 120 s |
| 3.6 Harness tools locked | 2026-10-11 | demo / us-east-1 | `invoke_demo.py "Run the shell command ..."` | PASS: no tool available, zero tool calls; `allowed_tools = ["@docs"]`; no `InvokeAgentRuntimeCommand` grant |
| 3.7 One tenant Gateway | 2026-10-11 | demo / us-east-1 | gateway `symphony-demo-gateway-kevk1n80ro` | PASS: AWS_IAM inbound, role may invoke only the document Lambda |
| 3.12 Customer-side role | 2026-10-11 | fixture 719453899809 / us-east-1 | `external-aws-connector` apply | PASS: trusts only `symphony-demo-documents-tool` with external ID; read-only `approved-docs/` |
| 3.13–3.14 Tool Lambda + target | 2026-10-11 | demo / us-east-1 | target `customer-documents` | PASS: exactly `list_documents`, `read_document` |
| 3.15 Approved read end to end | 2026-10-11 | demo → fixture | `invoke_demo.py "How many PTO days ..."` | PASS: list → read → "1.5 days per month, two weeks' notice", cited `handbook/pto-policy.md`; 573 tokens |
| 3.16 Denials | 2026-10-11 | demo → fixture | direct Lambda invoke + STS | PASS: `../forbidden-docs/...` → INVALID_ARGUMENT; wrong gateway → UNAUTHORIZED; tenant admin with correct external ID → AccessDenied on AssumeRole; agent leaked 0 canary lines |
| 3.18–3.19 Model smoke | 2026-10-11 | demo / us-east-1 | `bedrock-runtime converse` | PASS: Kimi K2.5 and GLM 5 answer in-Region on-demand |
| Repeatability | 2026-10-11 | all | `terraform plan -detailed-exitcode` | PASS: tenant-workload, external-aws-connector exit 0 |

Open: 3.1–3.4 (mTLS ingress, device enrollment, Cognito at ingress, route policy), 3.17 per-user authorization, 3.20–3.23 (A/B benchmark, latency trace, model choice, spend reconciliation).

## Phase 3b (2026-10-11, demo 603238661867 / us-east-1)

| Criterion | Command or test | Result |
| --- | --- | --- |
| ZDR set | `tenant_ops.py zdr` | PASS: `none` in us-east-1, us-east-2, us-west-2; Kimi K2.5 and Opus 4.5 still answer |
| ZDR locked | `put_account_data_retention(mode=default)` as AWSControlTowerExecution | PASS after SCP propagation: AccessDenied. Note: in the first ~2 minutes after attaching the SCP the call succeeded; mode was immediately restored to `none` |
| 1 Sandbox analysis | "total Q3 revenue by region" | PASS: `read_document` + `code_interpreter`; South 139,000 / West 114,000 / East 106,000 / Central 103,500; total 462,500 (matches local) |
| 2 Browser | allowlisted AWS docs page; `https://example.com` | PASS: summarized docs; example.com blocked by policy; recording written to `symphony-demo-browser-603238661867/recordings/` |
| 3 Memory | `--user ty` then new session; `--user jordan` | PARTIAL: facts extracted per actor (`/users/ty/facts`: "The user's name is Ty.", "Ty prefers answers formatted as tables."); jordan has 0 records (isolation PASS). Open defect: harness does not inject long-term records into new sessions although `retrievalConfig` is set and direct `RetrieveMemoryRecords` returns them |
| 4 KB search | `search_documents` "notice period"; "CANARY-FORBIDDEN" | PASS: cites `handbook/pto-policy.md`; canary never returned; 4 approved documents indexed, forbidden prefix never scanned |
| 5 Step-up | `--model-id us.anthropic.claude-opus-4-5-20251101-v1:0` | PASS: answered via US cross-Region profile. Opus 5.5 / GPT 6.1 Sol blocked pending AWS Support (account-level override) |
| 6 ZDR re-check | `tenant_ops.py zdr` | PASS: all `none` |
| 7 Traces | `tenant_ops.py lockdown-logs`; describe-log-groups | PASS: `aws/spans` and runtime log group use the tenant KMS key with 7-day retention; nothing exported |
| Repeatability | `plan -detailed-exitcode` | PASS: organization, tenant-baseline, tenant-workload, sandbox-fixture, external-aws-connector exit 0 |
