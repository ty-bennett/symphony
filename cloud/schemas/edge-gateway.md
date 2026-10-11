# Jetson edge gateway — requirements (v1)

What the on-prem team builds on the Jetson so OpenCode (and any OpenAI-compatible client) can use Symphony. It must fit on a **Jetson Orin Nano 8 GB** next to the local model, so the gateway itself is deliberately small.

## Budget on an Orin Nano 8 GB

The 8 GB is shared by CPU and GPU. Figures are planning estimates; confirm them on the device.

| Component | Memory | Notes |
| --- | --- | --- |
| JetPack OS, headless (no desktop) | about 1 GB | Disable the desktop session. |
| Local model: Qwen3-4B, 4-bit (Q4_K_M) via `llama.cpp` server with CUDA | about 3–3.5 GB | Weights about 2.5 GB plus KV cache for a 4k context. Serves both routing decisions and local answers. |
| **Edge gateway (this document)** | **≤ 64 MB resident** | One process, no GPU use. |
| Headroom | about 3 GB | Page cache, spikes, updates. |

Hard limits for the gateway: **≤ 64 MB resident memory** with 10 concurrent streams, **≤ 50 ms added latency** per request (excluding the model), **< 2% CPU when idle**, a **single static binary** (Go or Rust preferred; Python using only the standard library is acceptable), run as a **systemd unit** with no Docker requirement, and **arm64**.

## What it does

1. **OpenAI-compatible API on the office network:** `POST /v1/chat/completions` (with `stream: true`) and `GET /v1/models`, served over HTTPS at `https://symphony.local` (see TLS below).
2. **Local keys:** each person gets a random API key tied to their Cognito `sub`. Store a hash of the key, never the key itself. A request without a valid key gets 401.
3. **Routing:** decide `local-only`, `private-cloud`, or `frontier-eligible` for each request, and answer `local-only` on the Jetson. This decision is the data-tier ceiling; the cloud enforces it again.
4. **Cloud calls:** translate to the [v1 request envelope](request.v1.schema.json), call `api.<tenant>.symphony-edge.link` with the device certificate (mTLS) and the person's Cognito access token, then translate the [SSE events](sse-events.v1.md) back into OpenAI streaming chunks:

   | Symphony event | OpenAI chunk |
   | --- | --- |
   | `meta` | First chunk: `role: assistant`, with `model` set to the route's model |
   | `token` | `choices[0].delta.content` |
   | `tool` | Optional status text such as "Searching documents…" (or drop it); never the tool's inputs |
   | `done` | `finish_reason: "stop"` plus `usage` mapped from `inputTokens` / `outputTokens` |
   | `error` | An `error` object with the Symphony `code`, then close the stream (see [errors.md](errors.md)) |

5. **Tokens:** keep each person's refresh token encrypted at rest (key file mode 0600, or the Jetson's secure storage), and refresh access tokens before they expire (60 minutes). Never log tokens, prompts or answers.
6. **Device identity:** the device private key never leaves the Jetson. Enrollment and rotation follow [sse-events.v1.md](sse-events.v1.md#device-enrollment-operator-once-per-jetson).
7. **OpenCode setup and SSO:** serve `GET /.well-known/opencode` (OpenCode's organization manifest: `auth.command`, `auth.env`, `config`):

   ```json
   {
     "auth": { "command": ["symphony", "login", "--print-key"], "env": "SYMPHONY_API_KEY" },
     "config": {
       "provider": {
         "symphony": {
           "npm": "@ai-sdk/openai-compatible",
           "name": "Symphony",
           "options": { "baseURL": "https://symphony.local/v1", "apiKey": "{env:SYMPHONY_API_KEY}" },
           "models": { "auto": { "name": "Symphony" } }
         }
       }
     }
   }
   ```

8. **Sign-in endpoints** used by the `symphony login` tool:
   - `GET /auth/config` returns the hosted login base URL and the app client ID (from `terraform output hosted_login_domain` and `jetson_app_client_id`).
   - `POST /auth/exchange {code, code_verifier, redirect_uri}` exchanges the code at Cognito's `/oauth2/token`, stores the refresh token, and returns a new local key.

## Sign-in flow (once per laptop)

1. The person runs `opencode auth login https://symphony.local`.
2. OpenCode fetches the manifest and runs `symphony login --print-key`.
3. The tool listens on `http://localhost:53682/callback` and opens the browser to `<hosted login>/oauth2/authorize` with PKCE (S256), `scope=openid email profile`.
4. The person signs in, either with email, password and authenticator code, or with their company's SSO button when the tenant has `sso_identity_providers` configured.
5. The tool sends the code and verifier to `POST /auth/exchange`, then prints the local key to stdout.
6. OpenCode stores the key as `SYMPHONY_API_KEY` and applies the provider config.

The `symphony login` tool runs on the laptop. It should be just as small: one static binary, downloadable from `https://symphony.local/cli/<os>-<arch>`.

## TLS for `symphony.local`

Laptops must trust the gateway's certificate. The recommended option needs no root certificate installed on laptops:
1. Give each Jetson a DNS name such as `jetson-01.demo.symphony-edge.link` that resolves to its office-network IP.
2. Get a public certificate for that name by DNS validation in the tenant zone.

The alternative is a server certificate from the tenant's pilot CA, with that CA installed on laptops through device management.

## Behavior when the cloud is unreachable

`local-only` requests keep working. Other requests return an OpenAI `error` object with code `UPSTREAM_UNAVAILABLE` within 5 seconds. Never fall back silently to answering a cloud-tier prompt locally unless the person chose that.

## Acceptance checks on an Orin Nano 8 GB

- [ ] Resident memory ≤ 64 MB with 10 concurrent streaming requests (`ps`/`smem` over 10 minutes).
- [ ] p50 added latency ≤ 50 ms, from request in to first upstream byte.
- [ ] The local model and gateway together use ≤ 5 GB (`tegrastats`) under load.
- [ ] `opencode auth login https://symphony.local` completes with a Cognito user and with an SSO user.
- [ ] A revoked device returns `DEVICE_REVOKED` and a disabled user is refused within 60 minutes.
- [ ] Prompts, answers and tokens never appear in the gateway's logs.
- [ ] The gateway restarts cleanly after a power cycle (systemd) and serves again within 30 seconds.

## Open decision

OpenCode sends its own tool definitions (edit files, run shell commands) and expects the model to call them. The cloud agent runs its own tools and returns text. Proposed: send coding turns that carry OpenCode tools to the local model; let questions about company documents go to the cloud and come back as plain answers.
