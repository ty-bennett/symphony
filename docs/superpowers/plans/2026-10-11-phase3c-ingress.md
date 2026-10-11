# Phase 3c — Jetson ingress, streaming, and harness UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Jetson calls one mTLS endpoint per tenant with a Cognito user token and receives the tenant agent's answer as a server-sent-event stream, with the user's remembered facts applied and no prompt text in logs.

**Architecture:** API Gateway REST (Regional, custom domain `api.<tenant>.symphony-edge.link`, mTLS truststore in S3, execute-api endpoint disabled) → uncached Lambda REQUEST authorizer (device serial in the registry + Cognito access token) → Node.js 22 response-streaming Lambda that validates the v1 envelope, applies the data-tier policy, prefetches memory facts, calls `InvokeHarness` as the user, and streams SSE.

**Tech Stack:** Terraform 1.16.4 / AWS provider 6.68; Node.js 22 (`awslambda.streamifyResponse`, AWS SDK v3, `aws-jwt-verify`, esbuild); Python 3.12 (`cryptography`, boto3); `node:test`, `unittest`, `terraform test` with mocked providers.

**Spec:** `docs/superpowers/specs/2026-10-11-jetson-ingress-streaming-design.md`

## Global Constraints

- Endpoint `api.demo.symphony-edge.link`; parent zone `symphony-edge.link` lives in the management account; the tenant owns a delegated `demo.symphony-edge.link` zone.
- Stable error codes: `DEVICE_REVOKED` 403, `USER_UNAUTHORIZED` 401, `DATA_TIER_DENIED` 403, `INVALID_REQUEST` 400, `DUPLICATE_REQUEST` 409, `MODEL_UNAVAILABLE` 503, `UPSTREAM_TIMEOUT` 504.
- The tenant ID comes only from the authorizer context, never the body; unknown body fields are rejected.
- Memory prefetch: `/users/{sub}/facts`, top 5, ≤ 1,500 characters, 400 ms timeout, labeled untrusted.
- Audit: one JSON line per request, no prompt or answer text, user hashed.
- The pilot CA private key never enters Git or Terraform state (default `~/.symphony/pki/<tenant>/ca.key`); only the public `cloud/pki/<tenant>/ca.pem` is committed.
- No real account IDs in source; deploy-time values live in the gitignored `terraform.tfvars`.
- Deploy stop rule: stop on any destroy, replacement, or unexpected change.

## Review Focus

1. API Gateway's `clientCert.serialNumber` is a **decimal** integer (observed live); the registry stores plain lowercase hex — `authorize.normalizeSerial` converts decimal (and colon-hex) to the registry form.
2. A memory recall that throws (not just times out) must not fail the request — tested in `continues without facts when memory recall fails`.
3. Upstream SDK errors (throttling, timeouts) map to `MODEL_UNAVAILABLE` / `UPSTREAM_TIMEOUT`, never a silent fallback route.
4. A missing `Authorization` header is rejected by API Gateway before the authorizer runs (401), even for a revoked device — acceptable: both are denials before any model call.
5. The esbuild ESM bundle needs a `createRequire` banner so lazy `require` calls inside the AWS SDK work on Lambda.

---

## File map

| Path | Responsibility |
| --- | --- |
| `cloud/services/ingress/src/envelope.mjs` | v1 envelope validation, `RequestError` |
| `cloud/services/ingress/src/policy.mjs` | `choose_route` data-tier policy |
| `cloud/services/ingress/src/sse.mjs` | SSE framing |
| `cloud/services/ingress/src/core.mjs` | `handle()` request flow with injected dependencies |
| `cloud/services/ingress/src/authorize.mjs` | `authorize()` device + user checks, `normalizeSerial` |
| `cloud/services/ingress/src/ingress.mjs`, `authorizer.mjs` | Lambda entrypoints (AWS SDK wiring) |
| `cloud/services/ingress/test/*.test.mjs` | unit + contract tests |
| `cloud/schemas/` | API contract: OpenAPI, JSON Schema, SSE events, errors, examples |
| `cloud/services/agent/device_pki.py` | pilot CA, CSR signing, enroll / revoke |
| `cloud/services/agent/edge_client.py` | laptop stand-in for the Jetson (mTLS + Cognito + SSE) |
| `cloud/services/agent/eval_harness.py`, `evals/harness_v1.json` | 15-prompt harness evaluation (threshold 13) |
| `cloud/terraform/tenant-workload/ingress.tf` | DNS, ACM, truststore, Cognito client, idempotency table, Lambdas, API, domain, WAF, alarms |
| `cloud/terraform/tenant-workload/tests/ingress.tftest.hcl` | plan-mode tests for the above |
| `.github/workflows/services-offline.yml` | CI for Node and Python service tests |

### Task 1: Ingress request flow (Node)

**Files:** `cloud/services/ingress/{package.json,src/envelope.mjs,src/policy.mjs,src/sse.mjs,src/core.mjs}`, `test/envelope.test.mjs`, `test/core.test.mjs`

**Interfaces:**
- Produces: `parseEnvelope(body) -> {requestId, conversationId, dataTier, modelPreference, deadlineMs, messages}`; `choose_route({dataTier, modelPreference}, {default, stepUp}) -> {route, modelId}`; `handle({body, auth:{tenantId, deviceId, userSub}, write, deps:{claimRequest, recallFacts, invokeHarness, log, now}, config:{tenantId, models}}) -> audit`.

- [x] Tests: envelope defaults and rejections (tenant field, bad tier, version, short ID, empty / assistant-last / oversized messages, deadline), policy matrix, SSE framing; flow streams `meta`/`tool`/`token`/`done`, injects facts, audit has no text and a hashed user; local-only and wrong tenant denied before invoking; replayed `request_id` → `DUPLICATE_REQUEST`; recall timeout continues; throttling → `MODEL_UNAVAILABLE`, `TimeoutError` → `UPSTREAM_TIMEOUT`; recall error continues with `audit.memory = "error"`.
- [x] Run: `cd cloud/services/ingress && npm test` → all pass.

### Task 2: Authorizer and Lambda entrypoints (Node)

**Files:** `src/authorize.mjs`, `src/authorizer.mjs`, `src/ingress.mjs`

**Interfaces:**
- Produces: `authorize({clientCert, authorization, tenantId, deps:{getDevice, verifyToken, now}}) -> {principalId, context:{user_sub, device_id, tenant_id}}`, throwing `AuthError("DEVICE_REVOKED" | "USER_UNAUTHORIZED")`. The Lambda maps `USER_UNAUTHORIZED` to `throw "Unauthorized"` (gateway 401) and others to a Deny policy (gateway 403).
- Ingress env: `TENANT_ID, HARNESS_ARN, MEMORY_ID, DEFAULT_MODEL_ID, STEP_UP_MODEL_ID, IDEMPOTENCY_TABLE`. Authorizer env: `TENANT_ID, USER_POOL_ID, APP_CLIENT_ID, DEVICE_REGISTRY_TABLE`.

- [x] Tests: active/revoked/expired/unknown/other-tenant device; missing/invalid token; colon-hex serial normalized.
- [x] Build: `npm run build` (bundles the SDK, `createRequire` banner) → `dist/ingress.mjs`, `dist/authorizer.mjs`; smoke-import both.

### Task 3: API contract

**Files:** `cloud/schemas/{README.md,openapi.yaml,request.v1.schema.json,sse-events.v1.md,errors.md,examples/*}`, `cloud/services/ingress/test/contract.test.mjs`

- [x] Contract tests: validator enums equal the schema's; example requests parse and route (local-only → 403); message objects reject extra fields; example streams use only documented events.

### Task 4: Device PKI and stand-in client (Python)

**Files:** `cloud/services/agent/device_pki.py`, `edge_client.py`, `tests/test_device_pki.py`, `tests/test_edge_client.py`, `cloud/.gitignore`

**Interfaces:**
- Produces: `create_ca(tenant, key_path, cert_path)`, `make_device_csr(device_id) -> (key_pem, csr_pem)`, `enroll_device(table, ca_key, ca_cert, csr_pem, device_id, tenant) -> (cert_pem, item)`, `revoke_device(table, serial)`; registry item `{cert_serial, device_id, tenant_id, fingerprint_sha256, status, not_after, enrolled_at}`.

- [x] Tests: CA has CA:TRUE + keyCertSign, key is 0600, refuses overwrite; device cert is 1-year clientAuth, not a CA, issued by the CA; registry item keyed by plain hex with conditional put; tampered CSR and unsafe device IDs rejected; revoke marks `revoked`, unknown serial fails.
- [x] Run: `cd cloud/services/agent && uv run --no-project --with 'boto3>=1.43.111' --with 'cryptography>=46' python -m unittest discover -s tests`.
- [x] Create the real CA: `device_pki.py create-ca demo` (key → `~/.symphony/pki/demo/ca.key`, cert → `cloud/pki/demo/ca.pem`).

### Task 5: Terraform ingress

**Files:** `cloud/terraform/tenant-workload/{ingress.tf,variables.tf,providers.tf,outputs.tf}`, `tests/{ingress.tftest.hcl,agent.tftest.hcl,guard.tftest.hcl,terraform.tfvars,fixtures/truststore.pem}`

- [x] Tests (plan mode): tenant zone + NS delegation in the parent zone via `aws.management`; ACM DNS validation in the tenant zone; execute-api disabled; mTLS truststore URI and pinned object version; Regional TLS 1.2 domain + alias record; `AWS_PROXY` + `STREAM` + `response-streaming-invocations` URI; `CUSTOM` auth with uncached `REQUEST` authorizer; gateway responses 403 `DEVICE_REVOKED` / 401 `USER_UNAUTHORIZED`; throttling 20/40 and WAF 500/5 min; IAM statement lists for both roles; tenant from config; Jetson client has no secret and 60-minute tokens; idempotency TTL and tenant key; alarms on 5XX and 4XX spikes.
- [x] Run: `cloud/terraform/scripts/check.sh` → exit 0.

### Task 6: Harness UX and evaluation

**Files:** `cloud/terraform/tenant-workload/harness.tf` (system prompt), `tests/agent.tftest.hcl`, `cloud/services/agent/{eval_harness.py,evals/harness_v1.json,tests/test_eval_harness.py}`

- [x] Prompt test asserts tool order, memory-facts handling, one clarifying question, untrusted data.
- [x] Grader tests: tools, include/exclude, forbidden tools, stream errors, case-insensitive matching, 15 unique cases, threshold 13.

### Task 7: CI

- [x] `.github/workflows/services-offline.yml`: `npm ci && npm test && npm run build`; ruff; Python unittest for `services/agent` and `tools`.

### Task 8: Deploy and verify (live)

- [ ] Add `management_account_id` and `parent_zone_id` to the gitignored `tenant-workload/terraform.tfvars`; build `dist/`; plan (expect only creates + the harness prompt update); apply.
- [ ] Enroll a stand-in device: `device_pki.py make-csr laptop-01 ~/.symphony/devices/laptop-01` then `enroll demo laptop-01 …/device.csr …/device.pem`.
- [ ] Create test users `alice` and `bob` with `edge_client.py make-user`.
- [ ] Verify the spec's 8 criteria and record them in `cloud/evidence/phase-3.md`; re-plan with `-detailed-exitcode` = 0.

## Rulings

- Authorizer TTL 0 instead of the spec's 5-minute cache — revocation must take effect on the next request; cost: one registry read + JWT check per request.
- One ACM validation record instead of `for_each` — a single hostname, and the set is unknown at plan time.
- Jetson app client also allows `ADMIN_USER_PASSWORD_AUTH` — needed by the operator stand-in client; it requires IAM credentials, so a Jetson cannot use it.
- Alarms have no notification target in the pilot — avoids another email subscription; they are visible in the tenant console.
- The public CA certificate is committed (`cloud/pki/demo/ca.pem`) so any operator can re-plan without replacing the truststore; keys and CSRs are gitignored.
