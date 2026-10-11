# Symphony live demo runbook

A laptop stands in for the customer's Jetson: it holds a device certificate (mTLS) and signs a person in with Cognito, then calls the tenant endpoint `https://api.demo.symphony-edge.link/v1/requests` and streams the private agent's answer. Every command below is `cloud/demo/demo.sh`, run from the repository root. Total run time is about 10 minutes.

## What the audience should take away

1. The answer streams from an agent running in the customer's own AWS account, on a model that keeps nothing (zero data retention).
2. Only an enrolled device **and** a signed-in person get in; revoking a device takes effect on its next request.
3. The data-tier policy keeps `local-only` prompts on the Jetson; the frontier model is opt-in per request.
4. The agent remembers each person separately, and the audit log records who did what without storing what they said.

## One-time setup (already done on the presenter laptop)

| Item | Where |
| --- | --- |
| AWS operator profile `iamadmin-general` | `~/.aws/config` |
| Device certificates `laptop-01` (active), `laptop-02` (revoked) | `~/.symphony/devices/` |
| Demo users alice and bob (TOTP MFA enrolled by the client) | `~/.symphony/test-users/` |
| Cached endpoint values and user aliases | `~/.symphony/demo.env` (`demo.sh setup`) |
| Tools | `uv`, `terraform`, `curl` |

To rebuild on another laptop: `cloud/services/agent/device_pki.py make-csr laptop-01 ~/.symphony/devices/laptop-01`, then `device_pki.py --assume-role-arn <tenant role> enroll demo laptop-01 …/device.csr …/device.pem` (needs the CA key from `~/.symphony/pki/demo/` on the operator laptop), then `demo.sh setup` and `demo.sh make-user alice <email>`.

## 15 minutes before

```bash
aws sts get-caller-identity --profile iamadmin-general   # if expired: aws login --profile iamadmin-general
cloud/demo/demo.sh setup                                 # refresh cached values
cloud/demo/demo.sh warm                                  # first call after idle can take ~30 s
cloud/demo/demo.sh warm                                  # second call should start in ~3 s
```

Optional, for showing traces in the CloudWatch console (GenAI Observability): raise span indexing for the demo window, and lower it afterwards.

```bash
terraform -chdir=cloud/terraform/tenant-workload apply -var trace_indexing_percent=100   # before
terraform -chdir=cloud/terraform/tenant-workload apply                                    # after (back to 1%)
```

## Scenes

| # | Say | Run | Expect |
| --- | --- | --- | --- |
| 1 | "Alice asks a policy question from her Jetson." | `demo.sh ask alice "How much paid time off do full-time staff accrue?"` | `[status] 200 text/event-stream`, a `[tool] …search_documents` line, then "1.5 days … per month" citing `handbook/pto-policy.md`. First token ~3–7 s. |
| 2 | "She asks for analysis; the agent computes in a sandbox with no internet." | `demo.sh ask alice "What was total Q3 2026 revenue by region in the sales CSV?"` | Tools `read_document` and `code_interpreter`; South 139,000 · West 114,000 · East 106,000 · Central 103,500 · total 462,500. ~20 s. |
| 3 | "It remembers Alice — and only Alice." | `demo.sh ask alice "What is my name and which region do I manage?"` then `demo.sh ask bob "What is my name and which region do I manage?"` | Alice: "Alice … East region" (often as a table, her saved preference). Bob: "I don't know your name…". |
| 4 | "Sensitive prompts never leave the device." | `demo.sh local-only` | `[status] 403`, `DATA_TIER_DENIED`. No model was called. |
| 5 | "A frontier model only when the request is allowed to use one." | `demo.sh ask alice "In two sentences, why does zero data retention matter for a law firm?" --model step-up --data-tier frontier-eligible` | `[meta] … route private-cloud-step-up, model us.anthropic.claude-opus-4-5…`. Then the same with `--model step-up` alone → 403 `DATA_TIER_DENIED`. |
| 6 | "A stolen or retired Jetson is cut off." | `demo.sh revoked` | `403 {"code":"DEVICE_REVOKED"}`. |
| 7 | "No certificate, no connection. No sign-in, no answer." | `demo.sh no-cert` then `demo.sh no-token` | TLS connection reset; then `401 {"code":"USER_UNAUTHORIZED"}`. |
| 8 | "Every request is audited — without the conversation." | `demo.sh audit 5` | One JSON line per request: tenant, device, hashed user, route, model, tools, latency, tokens, status. No prompt or answer text. |

Optional scene (slow, about 2 minutes; pre-record if possible): `demo.sh ask alice "Using the AWS documentation website, what is Amazon Bedrock AgentCore in one sentence?"` shows the allowlisted browser; `demo.sh ask alice "Open https://example.com and tell me the page title."` shows the block.

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| `Your session has expired` / `No valid credential sources` | `aws login --profile iamadmin-general`, then repeat the command. |
| `ExpiredCodeException … software token has already been used` | A TOTP code was reused within 30 s; wait 30 s and repeat. The client caches tokens for an hour, so this only happens on fresh sign-ins. |
| First answer takes ~30 s | Cold start after idle or a harness update; run `demo.sh warm` again. |
| `Failed to resolve …amazonaws.com` | Local DNS blip (Tailscale resolver); wait a minute or toggle Tailscale. |
| `DEVICE_REVOKED` for laptop-01 | Check the registry item: `device_pki.py` enrollment output lists the serial; re-enroll if needed. |
| Wrong or invented user facts | Memory is per user; inspect with `RetrieveMemoryRecords` on `/users/<sub>/facts` and delete bad records (see `cloud/evidence/phase-3.md`). |

## After the demo

- Lower trace indexing (above) if you raised it.
- Optional regression check: `demo.sh eval` (15 graded prompts, passes at 13).
