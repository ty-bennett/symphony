# Jetson ingress, streaming, and harness UX (Phase 3c) — design

Status: approved in conversation 2026-10-11 (parts 1 and 2); awaiting written-spec review.
Builds on: `cloud/PLAN.md` Phase 3 items 3.1–3.4, the deployed tenant agent (`cloud/terraform/tenant-workload`), and `docs/superpowers/specs/2026-10-11-agent-tools-memory-models-design.md`.

## Intent

**Outcome.** A Jetson in the customer's office decides where each prompt goes (its own quantized Qwen for simple work, the cloud for everything else). For cloud-bound prompts it calls one endpoint that authenticates the device and the user, enforces the data-tier policy, adds the user's remembered facts, runs the tenant agent, and **streams the answer back** token by token to the user waiting on the Jetson.

**What the user said.** API Gateway + streaming Lambda (not direct-to-harness). Buy a cheap domain via Route 53; the user sends registrant details. Local pilot CA. The on-prem team builds the Jetson client; we publish the API contract. Memory recall comes from prefetching the user's facts before each call. Develop the harness so the UX is seamless.

**Assumptions (correct me).** One tenant (`demo`, us-east-1). Domain `symphony-edge.tech` (user's choice; Route 53 price $49/yr — `symphony-edge.click` at $3/yr is the cheap alternative), a single Terraform variable; endpoint `api.demo.symphony-edge.tech`. The Jetson holds a per-device client certificate and obtains a Cognito user token for the person using it.

**Success criteria (live).**
1. A valid device certificate + valid user token gets `text/event-stream` with the first token in ≤ 3 s (p50) for a document question; the full answer matches the non-streaming path.
2. A revoked or unknown device certificate → `403 DEVICE_REVOKED` before any model call.
3. A missing, expired, or wrong-pool user token → `401 USER_UNAUTHORIZED`.
4. `data_tier = local-only` → `403 DATA_TIER_DENIED` (the cloud never processes it).
5. The default `execute-api` hostname is disabled; only the mTLS custom domain answers.
6. Memory: after user A states a preference, a new session for A reflects it; user B's session does not.
7. Audit: one log line per request with request ID, tenant, device, user (hashed), route, model, tools, latency, tokens, and status — **no prompt or answer text**.
8. Evaluation set: at least 13 of 15 prompts pass their checks (right tools, key facts, refusals).

## Architecture

```
User → Jetson (router + local Qwen)
         │ HTTPS mTLS (device cert) + Authorization: Bearer <Cognito JWT>
         ▼
  API Gateway REST, Regional, custom domain api.demo.<domain>, mTLS truststore (S3)
  default execute-api endpoint disabled; WAF rate limit; stage throttling
         │ Lambda REQUEST authorizer (cached 5 min per cert + token)
         ▼
  Ingress Lambda (Node.js 22, arm64, response streaming, AWS_PROXY STREAM)
    1. validate request envelope (JSON schema v1)
    2. route policy (data tier → allowed path and model)
    3. prefetch memory facts for actorId (in parallel with 2)
    4. InvokeHarness(actorId, sessionId, model, messages + facts block)
    5. stream SSE events back; emit one audit log line
```

### Components

1. **Domain and TLS:** Route 53 registration of the chosen domain with privacy protection and a hosted zone (account decided in the plan). In the tenant account, a delegated `demo.<domain>` zone and an ACM certificate for `api.demo.<domain>` validated by DNS, so the tenant owns its records.
2. **Pilot CA and truststore:** the CA key is generated locally by an operator command and never stored in Git or Terraform state; the CA certificate is published to a versioned, encrypted S3 truststore bucket in the tenant account that the custom domain references.
3. **Device enrollment** (`tenant_ops.py enroll-device`, `revoke-device`): the Jetson creates its own key and CSR; the operator signs it (1-year validity) and writes `{cert_serial, device_id, tenant_id, fingerprint_sha256, status = active, not_after}` to the existing device registry table. Rotation: enroll a new certificate, keep both active briefly, then revoke the old serial. Revocation takes effect at the authorizer (API Gateway's truststore alone does not check revocation).
4. **Authorizer Lambda** (Node.js): reads the client-certificate serial from the mTLS context and requires an `active`, unexpired registry item for this tenant; verifies the Cognito JWT (issuer = tenant pool, `token_use = access`, client = the Jetson app client); returns `principalId = sub` with context `{device_id, tenant_id, user_sub}`. Denials map to `DEVICE_REVOKED` / `USER_UNAUTHORIZED` gateway responses.
5. **Cognito app client** on the existing pool for the Jetson: no secret; the login flow (auth code + PKCE or device-assisted) is the on-prem team's choice; 60-minute tokens.
6. **Ingress Lambda** (Node.js, `awslambda.streamifyResponse`):
   - Request envelope: `{version: 1, request_id, conversation_id, data_tier, deadline_ms, model_preference: "default" | "step-up", messages[]}`.
   - Policy: `local-only` → deny; `private-cloud` → default model; `frontier-eligible` with `step-up` → step-up model. The tenant ID comes only from the authorizer context.
   - Memory prefetch: `RetrieveMemoryRecords` on `/users/{sub}/facts`, top 5, total ≤ 1,500 characters, injected as a labeled, untrusted "Known facts about this user" block; 400 ms timeout (on timeout, continue without facts and log it).
   - `InvokeHarness` with `actorId = sub` and `runtimeSessionId` derived from `conversation_id`.
   - SSE events: `meta` (request_id, model, route), `token` (text delta), `tool` (tool name only), `done` (stop reason, usage), `error` (code, message).
   - Idempotency: `request_id` must be unique per user for 24 hours (DynamoDB conditional put with TTL), so a retry never re-runs tools.
7. **API contract** in `cloud/schemas/`: `openapi.yaml`, `request.v1.schema.json`, `sse-events.v1.md`, `errors.md`, and example requests — the on-prem team's source of truth.
8. **Harness UX:** refined system prompt (tool order `search_documents` → `read_document` → `code` → `web`; cite sources; short, streaming-friendly answers; one clarifying question when a request is ambiguous); handling of the memory-facts block; graceful wording on tool failures. Evaluation set `cloud/services/agent/evals/harness_v1.json` (15 prompts with expected tools and key facts) and runner `eval_harness.py` producing a pass/fail table; run before any prompt or model change.
9. **Audit and metrics:** one structured JSON log line per request (no text); CloudWatch metrics for TTFB, total latency, tokens, and denials; alarms on denial spikes and 5xx.

## Error handling

Stable codes: `DEVICE_REVOKED` 403, `USER_UNAUTHORIZED` 401, `DATA_TIER_DENIED` 403, `INVALID_REQUEST` 400, `DUPLICATE_REQUEST` 409, `MODEL_UNAVAILABLE` 503, `UPSTREAM_TIMEOUT` 504. Errors after streaming starts are sent as an SSE `error` event, then the stream closes. Harness or tool failures never fall back to a less private route.

## Testing

- **Offline:** Node `node:test` unit tests for the authorizer (active / revoked / expired / unknown certificate; valid / expired / wrong-pool token) and the ingress Lambda (envelope validation, policy matrix, prefetch timeout, SSE framing, idempotency, no text in the audit line) with mocked AWS SDK clients; `terraform test` for API Gateway (mTLS, default endpoint disabled, STREAM transfer mode, throttling), ACM/DNS, the truststore bucket, and roles; Python tests for enroll and revoke.
- **Live:** criteria 1–8 using a laptop stand-in client with a test device certificate and a test Cognito user, plus TTFB with and without memory prefetch.

## Out of scope

The Jetson client and local Qwen router (on-prem team), Phase 2 device packaging, Snowflake, multi-tenant onboarding automation, AWS Private CA.

## Cost notes

Domain $49/yr for `.tech` ($3/yr if `.click`) plus $0.50/month per hosted zone; API Gateway REST per request; Lambda streaming per GB-second and streamed bytes; WAF about $5/month plus rules; DynamoDB idempotency table on demand. All negligible at demo volume.
