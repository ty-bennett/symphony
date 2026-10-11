# Symphony implementation checklist

Use this as the working queue. [PLAN.md](PLAN.md) has the architecture, trust boundaries, and detailed acceptance criteria. Complete one small item at a time; check a box only after the result is verified. Record the command/test result, account and Region (where relevant), and any open defect in one `evidence/phase-N.md` file per phase. Never put credentials, certificates, prompts, or customer documents in evidence. A successful Terraform apply alone does not close a phase.

Do not deploy to an AWS account until its ID and Region are confirmed and its Terraform plan is reviewed. Do not start or enable Snowflake until the local and private AWS gates both pass. While AWS access is blocked, work only on offline code and tests. No Site-to-Site VPN, Tailscale, Kubernetes, or service mesh in the pilot.

**Next boxes:** 0.1–0.3. If AWS login is still blocked, work on 2.1–2.3 or 3.11 offline; do not skip an AWS gate to deploy.

## 0 — Inputs and safe preflight

- [ ] **0.1 Restore AWS access.** Run `aws login`, then `aws sts get-caller-identity` with the intended profile. Done: the account ID is confirmed as Symphony's management account; the current CLI session is expired.
- [ ] **0.2 Check existing AWS state (read-only).** Inspect Organizations and Control Tower in `us-east-1`. Done: record whether an Organization or landing zone already exists; stop if the start-from-scratch assumption is false.
- [ ] **0.3 Record pilot inputs.** Set synthetic tenant ID, workload Region (`us-east-1` unless there is a reason for `us-east-2`), account email addresses, DNS domain, budget amount, and alert contacts. Done: no placeholder is used in an apply.
- [ ] **0.4 Confirm pilot hardware.** Record the Jetson model, memory, OS image, and access method. Done: an agent can build and test on the actual device.
- [ ] **0.5 Define evaluation cases.** Write at least five simple local tasks, five private-cloud/tool tasks, and denied-route cases with expected outcomes. Done: the same cases can be replayed for model comparisons.
- [ ] **0.6 Set pass thresholds.** Record target quality, p95 first-token/complete latency, cost per completed task, and tool success rate. Done: model choice is based on measured thresholds, not preference alone.
- [ ] **0.7 Estimate pilot spend.** Price the shared accounts, logging/security baseline, API, AgentCore, Bedrock tests, and fixture account. Done: the estimate is reviewed against the pilot budget before provisioning.
- [ ] **0.8 Write the threat-model checklist.** Cover device loss, wrong tenant, prompt injection, credential theft, tool abuse, log leakage, and frontier egress. Done: each risk has a planned test or control.
- [ ] **Gate 0.** All required inputs are recorded; the management account is confirmed; no real customer data or secrets are in Git.

## 1 — AWS landing zone and cost controls

- [x] **1.1 Scaffold Terraform roots.** Create separate bootstrap, landing-zone, organization, shared-services, tenant-baseline, and tenant-workload roots. Done: `terraform fmt -check` and `terraform validate` pass where providers are available. Code: [`terraform/`](terraform/README.md); offline checks pass; evidence in [`evidence/phase-1.md`](evidence/phase-1.md).
- [x] **1.2 Pin tool versions.** Set Terraform and AWS provider constraints and commit the provider lock file. Done: a clean init reproduces the same provider selection. Code: [`terraform/`](terraform/README.md); offline checks pass; evidence in [`evidence/phase-1.md`](evidence/phase-1.md).
- [x] **1.3 Build the state backend.** Configure a versioned, KMS-encrypted S3 bucket and state locking in the management account. Done: state is remote, recoverable, and not committed to Git. Deployed: `s3://symphony-tfstate-695911680545`, KMS, native locking. Evidence: `evidence/`.
- [ ] **1.4 Add account guards.** Make each Terraform root check the expected account ID and Region before apply. Done: a wrong-profile plan fails safely.
- [x] **1.5 Create the Organization.** Enable all features and create the Security OU first. Done: Organizations shows the expected management boundary with no customer workload in management. Adopted existing `o-r2xq6g9vo2` (import, 0 destroys). Evidence: `evidence/`.
- [x] **1.6 Create security accounts.** Create separate log archive and audit accounts with unique emails. Done: each account has the expected ID and OU placement. Log Archive 728330702611, Audit 520168725155 in Security OU. Evidence: `evidence/`.
- [x] **1.7 Establish Control Tower.** Apply the reviewed landing-zone manifest in `us-east-1`, governing `us-east-1` and `us-east-2`. Done: Control Tower reports healthy before account vending continues. LZ 4.0 ACTIVE / IN_SYNC. Evidence: `evidence/`.
- [x] **1.7a Register remaining OUs.** Create or register Infrastructure, Customers, and Sandbox through the supported Control Tower workflow. Done: account vending targets only governed OUs. AWSControlTowerBaseline 5.0 SUCCEEDED on all three. Evidence: `evidence/`.
- [x] **1.8 Create shared services.** Vend the shared-services account and apply only its baseline. Done: deployment automation and artifacts are outside management and tenant accounts. 130237968226 in Infrastructure. Evidence: `evidence/`.
- [x] **1.9 Create the synthetic tenant.** Vend one customer workload account and apply its regional baseline. Done: account ID and selected Region match the configuration. `demo` 603238661867, us-east-1. Evidence: `evidence/`.
- [x] **1.10 Create the fixture account.** Vend a low-cost separate account for cross-account S3 tests. Done: a test bucket and approved prefix exist with synthetic data only. 719453899809 with approved/forbidden synthetic docs. Evidence: `evidence/`.
- [ ] **1.11 Enable central audit.** Verify CloudTrail and Config events arrive in log archive. Done: an event from the tenant account is queryable there.
- [x] **1.12 Enable delegated security.** Configure GuardDuty and Security Hub in the audit account. Done: a test finding reaches the intended administrator. Sample GuardDuty finding emailed to the security contact. Evidence: `evidence/`.
- [ ] **1.13 Set human access.** Configure Identity Center permission sets and MFA for operators and pilot customer admins. Done: test users can enter only assigned accounts.
- [ ] **1.14 Create the customer budget.** Add 80% forecast, 80% actual, and 100% actual alerts for the linked tenant account. Done: recipients and a controlled notification test are verified; do not call this a hard cap.
- [x] **1.15 Check repeatability.** Re-run plans for the landing zone and tenant baseline. Done: no unexpected account, key, or log-bucket replacement is proposed. Every root re-plans with exit 0. Evidence: `evidence/`.
- [ ] **Gate 1.** Healthy landing zone, isolated tenant and fixture accounts, central logs, security finding, identity test, and budget notification are evidenced.

## 2 — Local Jetson route

- [ ] **2.1 Choose one local model candidate.** Record license, weights checksum, quantization, and measured device fit. Done: artifact and load command are pinned.
- [ ] **2.2 Create the local service skeleton.** Add an ARM64 service/container, health endpoint, and systemd startup. Done: it restarts without manual intervention.
- [ ] **2.3 Implement local inference.** Expose a versioned local request/response contract. Done: a synthetic prompt returns an answer and model ID.
- [ ] **2.4 Implement the deterministic tier guard.** Enforce `local-only`, `private-cloud`, and `frontier-eligible` as maximum destinations. Done: unit tests show model output cannot widen a tier.
- [ ] **2.5 Add the simple-task router.** Use rules or a small classifier to prefer local work. Done: every evaluation task records route and reason code.
- [ ] **2.6 Add offline behavior.** Handle cloud/network loss without switching to a less private destination. Done: `local-only` and approved local fallback tests pass with networking disabled.
- [ ] **2.7 Add device health metrics.** Capture uptime, model load failure, memory, temperature, and latency without raw prompts. Done: metrics can be inspected locally.
- [ ] **2.8 Add signed configuration update and rollback.** Reject invalid signatures and keep last-known-good configuration. Done: both rejection and rollback are tested.
- [ ] **2.9 Benchmark the real Jetson.** Record p50/p95 first-token and complete latency, throughput, memory, and thermal behavior. Done: numbers come from the pilot device, not a laptop.
- [ ] **2.10 Test the head-swap idea separately.** Keep it only if it beats the simpler router on the same tasks without violating memory/latency limits. Done: decision and measurements are recorded.
- [ ] **Gate 2.** Local simple-task quality and latency pass; restart, offline, invalid-config, and no-egress tests pass. Do not start Snowflake.

## 3 — Private AWS model, Harness, and tools

### Ingress and agent

- [ ] **3.1 Create tenant ingress.** Deploy one Regional API Gateway custom domain with TLS/mTLS; disable the default `execute-api` endpoint. Done: only the intended hostname accepts a request.
- [ ] **3.2 Add device enrollment.** Issue one certificate per pilot device and record serial/fingerprint/status in a tenant registry. Done: valid device succeeds and revoked device fails.
- [ ] **3.3 Add end-user authentication.** Validate a pilot Cognito JWT at ingress. Done: missing, expired, and wrong-tenant tokens fail.
- [ ] **3.4 Enforce server-side route policy.** Recheck data tier and tool allowlist at ingress. Done: crafted requests cannot select Snowflake or an unapproved tool.
- [x] **3.5 Deploy one managed AgentCore Harness.** Set `converse_stream`, disabled persistent memory, and bounded tokens/iterations/time. Done: a no-tool synthetic request completes in the tenant Region. `symphony_demo_agent` on Kimi K2.5. Evidence: `evidence/`.
- [x] **3.6 Lock down Harness tools.** Set an explicit Gateway-only `allowedTools` list; deny direct Runtime command permission. Done: shell, file operations, and command API tests fail closed. `allowed_tools = ["@docs"]`; shell request made zero tool calls. Evidence: `evidence/`.
- [x] **3.7 Create one tenant Gateway.** Give it only the IAM permissions required to invoke approved targets. Done: it cannot invoke an unrelated Lambda or cross-tenant target. IAM-only; role invokes only the document Lambda. Evidence: `evidence/`.

### First custom connector

- [x] **3.8 Create a separate tool catalog.** [`tools/`](tools/README.md) lists AWS-managed options and keeps customer tool code separate.
- [x] **3.9 Implement the S3 document Lambda.** [`customer_documents/`](tools/customer_documents/README.md) contains `list_documents`, `read_document`, a Gateway schema, and a publishing manifest.
- [x] **3.10 Test the tool offline.** Sixteen unit tests pass; Ruff lint and format checks pass. This does **not** prove AWS integration.
- [ ] **3.11 Package the Lambda.** Build a reproducible artifact and pin its runtime/dependencies. Done: a local handler smoke test and package checksum are recorded.
- [x] **3.12 Deploy the customer-side role template to the fixture.** Scope it to one bucket/prefix, one trusted tenant role, and one external ID. Done: reviewed IAM policy has no write/delete or broad discovery permission. `external-aws-connector`. Evidence: `evidence/`.
- [x] **3.13 Deploy the tenant tool Lambda.** Set its fixed bucket, prefix, role ARN, Gateway IDs, and least-privilege execution role. Done: no model argument can change those values. Graviton, fixed config. Evidence: `evidence/`.
- [x] **3.14 Register the Gateway target.** Attach only the two read-only tool schemas and a default-deny policy. Done: Gateway lists precisely the expected fully qualified tool names. `customer-documents` with exactly two tools. Evidence: `evidence/`.
- [x] **3.15 Run a real approved read.** Call `list_documents` and `read_document` through Harness → Gateway → Lambda → STS → fixture S3. Done: trace and enabled fixture S3 data events can be correlated in CloudTrail. Harness → Gateway → Lambda → STS → fixture S3 answered with a citation. Evidence: `evidence/`.
- [ ] **3.16 Run denial tests.** Test wrong external ID, bucket/prefix, tenant, tool, revoked role, and write attempt. Done: no denied call returns document content.
- [ ] **3.17 Resolve per-user authorization before widening scope.** The current tool is tenant-wide read-only; Gateway Lambda metadata does not establish an end-user identity. Done: either all pilot users share the approved scope, or a trusted identity design and cross-user tests pass before enabling different scopes.

### Model choice and release gate

- [ ] **3.18 Smoke-test Kimi K2.5.** Confirm account/Region access and one single-tool and one repeated-tool request. Done: exact model ID, response, and failure are recorded.
- [ ] **3.19 Smoke-test GLM 5.** Run the same calls with the same Harness/tool configuration. Done: comparable results are recorded.
- [ ] **3.20 Run the A/B evaluation.** Replay the full task set for both models; optionally add Qwen3 32B as a cost baseline. Done: quality, tool success, failures, tokens, and cost per completed task are comparable.
- [ ] **3.21 Trace latency.** Record p50/p95 first-token and total time plus model, Harness, Gateway, Policy, Lambda, and STS spans; compare cold and warm calls. Done: the dominant latency source is identified from measurements.
- [ ] **3.22 Choose the private default.** Select the least-cost model meeting agreed quality, tool, latency, and privacy thresholds. Done: the decision and rejected candidates are documented; no choice is assumed ahead of testing.
- [ ] **3.23 Verify cloud spend reporting.** Reconcile request token estimates with AWS usage/budget reporting. Done: customer and Symphony can see usage without raw prompt logs.
- [ ] **Gate 3.** The Jetson-to-private-cloud route works in-region; negative security tests pass; tool revocation works; trace, latency, and budget evidence exist. Snowflake remains off until both Gates 2 and 3 pass.

## 4 — Optional Snowflake frontier route (only after Gates 2 and 3)

- [ ] **4.1 Record customer opt-in.** Approve frontier data categories, users, models, connectors, Region policy, and spend amount. Done: a disabled-by-default policy is versioned.
- [ ] **4.2 Confirm Snowflake account isolation.** Decide whether the provisional one-account-per-customer design is retained. Done: ownership, billing, and residency implications are approved before provisioning.
- [ ] **4.3 Provision one pilot Snowflake account and service identity.** Give the adapter only needed REST inference rights. Done: credentials are outside Git/Terraform state.
- [ ] **4.4 Build the tenant Snowflake adapter.** Add allowlisted model IDs, authenticated HTTPS calls, request IDs, timeout, and explicit failure handling. Done: a synthetic eligible request works with no tools.
- [ ] **4.5 Add the controlled tool-call loop.** Let the model propose a call, then authorize it through the tenant policy and Gateway before returning a capped result. Start with a separate synthetic frontier-approved tool; the private S3 document bundle is not frontier-enabled. Done: model-supplied URLs, account IDs, and role ARNs cannot select targets.
- [ ] **4.6 Block private data egress.** Classify request content and each tool result independently. Done: `local-only` and `private-cloud` test content causes zero Snowflake calls.
- [ ] **4.7 Add separate cost controls.** Configure Snowflake usage attribution, budget alerts, and customer reporting. Done: a test request joins Snowflake usage to a Symphony request ID.
- [ ] **4.8 Compare against private AWS.** Measure quality, complex-tool success, p95 latency, and cost per completed task. Done: a customer-visible rule explains when Snowflake is worth using.
- [ ] **4.9 Test immediate disable.** Turn off the frontier route and retry an eligible request. Done: no Snowflake call occurs after disable.
- [ ] **Gate 4.** Only opted-in, eligible requests reach approved frontier models and tools; prohibited requests do not egress; costs and regions are verified.

## 5 — Repeatable customer onboarding

- [ ] **5.1 Automate tenant account vending and baseline.** Onboard a second synthetic tenant from configuration only. Done: no copied credentials or manual IAM edits.
- [ ] **5.2 Automate device enrollment and rotation.** Issue, rotate, and revoke a second device certificate. Done: old/revoked device access fails.
- [ ] **5.3 Package the external AWS role for customers.** Provide reviewed Terraform and CloudFormation options plus removal steps. Done: customer can grant and revoke access without joining Symphony's Organization.
- [ ] **5.4 Add tenant-scoped tool publishing.** Validate schema, manifest, tests, IAM scope, version, and rollback before a tool appears in a catalog. Done: a customer-authored tool is visible only to its tenant.
- [ ] **5.5 Add minimum self-service views.** Show users, approved integrations, models, usage, alerts, and connection health. Done: customer admins cannot modify central security/billing controls.
- [ ] **5.6 Prove isolation.** Replay wrong-tenant model, tool, key, log, budget, and connector tests against both tenants. Done: all cross-tenant attempts fail.
- [ ] **5.7 Rehearse safe teardown.** Remove optional second-tenant workloads while retaining required audit records. Done: reviewed plan does not delete shared security resources.
- [ ] **Gate 5.** A second synthetic tenant can be onboarded and isolated repeatably; runbooks cover setup, revoke, recover, and teardown.

# Future decisions to be made

This is a parking lot for Claude and Symphony to spec together when the relevant phase or a real customer requires it. It does not waive existing pilot gates: set pilot pass thresholds in 0.6, keep the first document tool within the approved tenant-wide scope in 3.17, and complete the Phase 4 data-egress tests before enabling Snowflake.

- [ ] **F.1 End-user tool authorization — before users need different resource access.** Decide how a verified user identity reaches AgentCore Gateway and each tool, where per-user policy is enforced, how sessions are isolated, and which cross-user denial tests must pass. Until then, the pilot document tool remains tenant-wide and read-only.
- [ ] **F.2 Full data-classification policy — before real customer data or frontier access.** Decide who labels requests and documents, the default for unclassified content, how tool results affect the effective data tier, and who can approve reclassification. Unknown content must not silently reach Snowflake; model output cannot authorize a less private route.
- [ ] **F.3 Hard spend behavior — before promising a spend limit.** Decide whether limits are alert-only or enforced per request/day/month, what happens when metering is delayed or a limit is reached, and whether local AI remains available when paid cloud routes stop. AWS budget notifications alone are not a hard cap.
- [ ] **F.4 Production acceptance targets — before a real-customer release.** Set availability, p95 latency, quality, tool-call correctness, and cost-per-task targets, plus the owner of each go/no-go decision. The synthetic pilot still needs its measurable thresholds under 0.6.
- [ ] **F.5 Customer data and account lifecycle — before a real-customer release.** Decide retention periods, export format, integration revocation, account ownership or transfer at exit, deletion evidence, and customer-visible support access.
- [ ] **F.6 Operations and recovery — before a real-customer release.** Set backup scope, recovery time and data-loss targets, incident response and notification owners, and device certificate/key recovery procedures.
