# Cognito accounts, Jetson gateway, and coding on Orchestra — design

Status: draft for review (2026-10-11).
Builds on: `docs/superpowers/specs/2026-10-11-jetson-ingress-streaming-design.md` (deployed), `cloud/schemas/edge-gateway.md`, and the Symphony app in `sebastian/` (Soloist and Orchestra).

## Intent

**Outcome.** Everyone signs in to the Symphony app with SSO, through Cognito. Each prompt that OpenCode sends goes to a tiny server on the Jetson. That server sends it to Soloist (the local model) or Orchestra (the private cloud) according to the job's route, and streams the answer back, so OpenCode carries on the same way whichever place answers. Coding jobs work on Orchestra, Orchestra's browser is the one used when a job needs the web, and every job's spend is counted from the model that actually answered.

**What the user said.**
- Accounts must be backed by Cognito.
- People authenticate with SSO first.
- A super-minimal API server on the Jetson gives OpenCode one endpoint to hit for every prompt; the Jetson decides the route, and OpenCode continues down that path.
- Coding jobs must work on Orchestra.
- Orchestra's browser is preferred whenever a browser is needed.
- Spending is counted for whichever model the routing picked.
- Show costs as an estimate table.

**Assumptions (correct me).**
- The app, `opencode serve`, the local model (`llama.cpp`) and the gateway all run on the same Jetson, so OpenCode reaches the gateway on `127.0.0.1` and no network TLS is needed on the box.
- The route is decided once per job, when the job starts, and every later turn of that job stays there. A conversation that started on Soloist is never sent to the cloud partway through without the person choosing it.
- The app keeps making the routing decision (`routeTask()`, later the JEV model). The app runs on the Jetson, so the Jetson is what decides; the gateway enforces that decision on every prompt.

**Success criteria.**
1. Opening the app with no session goes straight to the Cognito login page. With SSO configured, the company's button is there, and signing in lands on Today. No passwords are stored on the box.
2. The owner role comes from the Cognito `owner` group. Owners can invite and remove people from Team, which creates or disables Cognito users through the cloud. A member can't reach owner pages or APIs.
3. Every OpenCode model request goes through the gateway. A Soloist job's prompts never leave the box (proven by the gateway log and zero cloud requests). An Orchestra job's prompts reach the cloud with the job starter's identity.
4. A coding job on Orchestra completes an edit-and-test loop: the model returns tool calls, OpenCode runs them on the box, and the results go back to the model. Same check on Soloist.
5. With a job that needs a web page, OpenCode uses Orchestra's browser tool, and its own `webfetch` is off. The page is opened by the AgentCore Browser in the tenant account, from the allowlist, and recorded.
6. Each job's spend equals the gateway's ledger for that job: tokens × the price of the model that answered (Soloist $0). The monthly limit uses that number.
7. The gateway stays at or under 32 MB resident and adds 20 ms or less per request on an Orin Nano. It's one static arm64 binary with no dependencies.

## Architecture

```
Browser ──► Symphony app (Next.js, on the Jetson) ──► opencode serve (127.0.0.1:4096)
   ▲  Cognito hosted login (SSO)        │ job start: register {session, place, user}
   │                                    ▼
   │                         symphony-gateway (Go, 127.0.0.1:8088)  ◄── every model request from OpenCode
   │                           │ Soloist ─► llama.cpp (127.0.0.1:8080, OpenAI-compatible, tools)
   │                           │ Orchestra ─► api.<tenant>.symphony-edge.link  (mTLS device cert + user token)
   │                           │                 ├─ POST /v1/chat/completions   model with OpenCode's tools (new)
   │                           │                 ├─ POST /v1/requests           agent: documents, browser (exists)
   │                           │                 └─ /v1/admin/users             Team management (new)
   │                           └ /mcp: orchestra_browse, orchestra_search_documents (Orchestra's tools for OpenCode)
   └──────────── usage ledger ◄─┘  (spend per job, per place, per model)
```

## Components

### 1. Accounts on Cognito, SSO first

- **Sign-in:** the app's middleware sends anyone without a session to `/api/auth/login`, which redirects to the Cognito login page using the code flow with PKCE. If a tenant has exactly one SSO provider, it adds `identity_provider=<name>` to go straight to that company's login. `/api/auth/callback` exchanges the code, verifies the ID token (issuer = the tenant pool, audience = the app client), and creates the session.
- **Sessions:** the cookie (`symphony_session`, HttpOnly, SameSite=Lax, Secure on HTTPS) holds a random ID. The server keeps `{sub, email, name, role, refresh token}`, with the refresh token encrypted (AES-GCM, key file mode 0600) in `.symphony/sessions.json`. Signing out revokes the refresh token at Cognito.
- **Roles:** Cognito groups `owner` and `member` (Terraform). No group means member. The operator puts the business owner in `owner` at onboarding.
- **First run:** `/setup` no longer creates accounts. After the owner's first sign-in, it walks through the existing business, privacy and spending steps.
- **Team:** invite, list and remove go through a new cloud admin API, `/v1/admin/users`, behind the same device and user authorizer:
  - The Lambda checks that the caller's token has `cognito:groups` containing `owner`.
  - Invite runs `AdminCreateUser`, which sends Cognito's invitation email, then adds the person to `member`.
  - Remove runs `AdminDisableUser` and `AdminUserGlobalSignOut`.
  - List runs `ListUsers`.
  
  No AWS credentials live on the box. With SSO, the owner mostly assigns people at the company's identity provider; invites are for people without a work account.
- **Removed from the app:** local passwords (scrypt), invite codes, `/join`, `/api/auth/setup`, and the lockout logic.
- **Kept:** `checkAuth()` and the owner/member checks. Their user `id` becomes the Cognito `sub`.

### 2. symphony-gateway (Go, one binary, standard library only)

- `POST /v1/chat/completions` (OpenAI-compatible, streaming) is the single endpoint OpenCode's `symphony` provider points at. It reads `x-opencode-session-id` (OpenCode 1.18.35 sends it on every request) and looks up the session's route:
  - **Soloist** (`device`): proxied unchanged to `llama.cpp` at `127.0.0.1:8080`.
  - **Orchestra** (`private`): forwarded to the cloud's `/v1/chat/completions` with the device certificate and the job starter's access token, which the gateway gets from the app over a localhost call guarded by a shared secret.
  - **Unknown session** (for example, the Coding agent tab): Soloist, the most private default, unless the owner picks Orchestra for that session.
- `POST /internal/sessions {session, place, user_sub, model}` is called by the app when a job starts, guarded by the shared secret. A child session (subagent) inherits its parent's route, using `x-opencode-parent-session-id`.
- **Usage ledger:** from each final chunk's `usage`, append `{time, session, user, place, model, input, output, cache, cost}` to `.symphony/usage.jsonl`. Cost uses a price table in the gateway's config: Soloist $0, Kimi K2.5 $0.60 / $3.00 per million tokens, Opus 4.5 $5.50 / $27.50. `GET /usage?session=` and `GET /usage?month=` return totals.
- **Response rewriting:** `model` becomes `soloist/<model>` or `orchestra/<model>`, so the app and OpenCode show what answered.
- **`/mcp`** (minimal MCP over HTTP: `initialize`, `tools/list`, `tools/call`) offers Orchestra's tools to OpenCode:
  - `orchestra_browse(url, question)` and `orchestra_search_documents(query)` both call the cloud agent (`/v1/requests`, which runs the AgentCore Browser and the knowledge base) and return its text.
  - Only Orchestra sessions may use them: the app sets the permission `orchestra_*: deny` on Soloist sessions.
- **Limits and hygiene:** listens on `127.0.0.1` only, ≤ 32 MB resident, no prompt or answer text in logs, a systemd unit, builds with `GOOS=linux GOARCH=arm64 CGO_ENABLED=0`.

### 3. Coding on Orchestra: a model endpoint that leaves the tools to OpenCode

- **New ingress route:** `POST /v1/chat/completions`, using the same mTLS domain, authorizer, WAF, throttling and audit.
  - The body is OpenAI chat format (messages with tool calls and tool results, `tools`, `stream: true`), plus the header `x-symphony-data-tier: private-cloud | frontier-eligible`. `local-only` is refused with 403.
- **The Lambda:**
  - Translates the request into Bedrock **ConverseStream**: system prompt, messages, `toolUse` / `toolResult`, `toolConfig` from OpenCode's tools.
  - Streams OpenAI chunks back: `delta.content`, `delta.tool_calls`, `finish_reason`, `usage`.
  - Uses Kimi K2.5 by default; `model: orchestra-step-up` with `frontier-eligible` uses Opus.
  - Its IAM role may invoke only the tenant's allowlisted models, ZDR stays in force, and the audit line holds no text.
- **Why this way:** OpenCode keeps running its own edit, shell and test tools on the box, against the files on the box. Orchestra supplies only the model's thinking, in the customer's private cloud account. The agent path (`/v1/requests`) stays for documents and the web.
- **To verify live:** Kimi K2.5 tool use through ConverseStream on Bedrock. If Kimi can't do tool calls reliably, Orchestra coding uses Opus 4.5 for coding sessions only, and the plan records that.

### 4. Orchestra's browser preferred

The OpenCode config the app generates turns off OpenCode's own `webfetch` (`"tools": {"webfetch": false}`) and adds the `orchestra` MCP server at `http://127.0.0.1:8088/mcp`. Any job that needs a web page then uses `orchestra_browse`: the tenant's AgentCore Browser, with the allowlist, in the private cloud, and recorded to the tenant bucket. The routing already sends jobs that need the web to Orchestra. The browser allowlist (`browser_allowed_domains`) becomes a per-business setting at onboarding.

### 5. Spending from the model that answered

The app's monthly total and each job's cost come from the gateway ledger (`/usage`), not from OpenCode's own cost figure. OpenCode only knows the one `symphony` provider and can't price Soloist and Orchestra separately. "Saved" still compares the same tokens against frontier prices. The spending limit works as before, with Orchestra jobs paused or moved to Soloist.

## Cost estimate

Prices: AWS Price List API, us-east-1, 2026-10-11. Token sizes: measured on the demo tenant, or assumed where noted.

| Item | Unit | Estimate |
| --- | --- | --- |
| Tenant baseline (WAF, Security Hub, Config, GuardDuty, KMS, DNS, alarms, storage) | per business per month | $13–20 |
| Symphony shared accounts, split across 10 businesses | per business per month | $2–4 |
| Jetson gateway, app, Soloist on the box | per request | $0 (electricity aside) |
| Document question via the Orchestra agent (Kimi K2.5) | per request | about $0.013 |
| Web page via Orchestra's browser (about 2 minutes) | per page | about $0.03 |
| Analysis in Code Interpreter | per request | about $0.02 |
| Frontier step-up via the agent (Opus 4.5) | per request | about $0.065 |
| Coding job on Orchestra, Kimi K2.5 (assumed 10 model turns × 25k input + 1k output) | per job | about $0.18 |
| Coding job on Orchestra, Opus 4.5 (same size) | per job | about $1.65 (prompt caching could cut about 60%) |
| New cloud pieces (chat Lambda, admin Lambda, Cognito groups) | per request | under $0.0002 |
| Cognito sign-in (Lite tier; federated SSO users are priced per active user) | per user per month | under $0.02 |
| Typical seat (525 requests: 85% documents, 10% analysis or web, 5% frontier) | per seat per month | about $10 |
| Heavy coding seat (adds 40 Kimi coding jobs a month) | per seat per month | about $17 |
| Onboarding labor (6–10 hours) | per business, one time | $600–1,000 internal |
| Jetson Orin Nano Super / Orin NX 16GB / AGX Orin, landed | per device, one time | about $500 / $1,300–1,500 / $3,700 |

## Error handling

| Case | What happens |
| --- | --- |
| Gateway can't reach the cloud | Orchestra requests return an OpenAI error (`UPSTREAM_UNAVAILABLE`) within 5 seconds. Soloist keeps working. Never a silent switch to Soloist. |
| The person's token can't be refreshed (disabled, or signed out everywhere) | 401 to OpenCode; the app shows "Sign in again". |
| Spending limit reached | The app pauses Orchestra jobs, or routes new ones to Soloist (existing rule). The gateway refuses Orchestra for a session the app marked paused. |
| Model refuses tool use, or returns malformed tool calls | The error streams back to OpenCode as-is, with no retry loop on the gateway. |

## Testing

- **Go gateway (`go test`):** routing per registered session and the parent fallback; Soloist traffic never dialing the cloud (fake upstreams); OpenAI streaming passthrough; model rewriting; ledger math against the price table; the MCP handshake; refusal without the shared secret; a race test; a memory benchmark.
- **Cloud (`node:test` + `terraform test`):** OpenAI ↔ ConverseStream translation (text, tool calls, tool results, usage); data-tier refusal; the admin API's owner check; Cognito groups; IAM limited to allowlisted models; the new routes behind the authorizer.
- **App (`check:logic`, `check:auth`):** PKCE and callback state; ID-token verification (wrong issuer or audience refused); roles from groups; Team calls the admin API; jobs register with the gateway; spending from the ledger.
- **Live:** success criteria 1–7 with a real Cognito user and an SSO test identity provider. This needs a test Entra ID or Google Workspace tenant from the user.

## Out of scope

The JEV routing model itself (it still plugs into `routeTask()`), the Jetson OS image and packaging, and multi-box offices.

## Open questions

1. Which identity provider do we test SSO against: your Google Workspace, or a free Microsoft Entra test tenant?
2. Whose identity should Coding agent sessions (started outside Today) use when they run on Orchestra? Proposed: the owner who has the tab open; members can't use that tab.
