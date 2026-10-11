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
