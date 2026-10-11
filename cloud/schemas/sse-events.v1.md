# Response stream (v1)

`POST /v1/requests` answers with `Content-Type: text/event-stream`. Each event is

```
event: <name>
data: <one line of JSON>

```

| Event | When | `data` |
| --- | --- | --- |
| `meta` | first, once | `{"request_id", "route", "model"}`. `route` is `private-cloud` or `private-cloud-step-up`. |
| `tool` | each time the agent starts a tool | `{"name"}`, for example `docs___search_documents`, `code`, `web`. Show it as a status line ("Searching documents…"); arguments and results are never sent. |
| `token` | as text arrives | `{"text"}`, a fragment to append to the answer. |
| `done` | last, on success | `{"stop_reason", "usage": {"inputTokens", "outputTokens", "totalTokens"} \| null}` |
| `error` | last, on failure | `{"code", "message"}`; see [errors.md](errors.md). The stream then closes. |

Client rules:

- Render `token` text as it arrives; do not wait for `done`.
- Exactly one of `done` or `error` ends a stream. A connection that closes without either is a network failure: retry with a **new** `request_id` only if the user asks again, because the agent may already have run tools.
- Ignore unknown event names (forward compatibility).
- Answers cite documents by key (for example `approved-docs/handbook/pto-policy.md`) and web pages by URL inside the text.

## Device enrollment (operator, once per Jetson)

1. The Jetson generates its own private key and a CSR with `CN=<device-id>` (lowercase letters, digits, dashes). The key never leaves the device.
2. The operator signs it: `python cloud/services/agent/device_pki.py enroll <tenant> <device-id> device.csr device.pem`. This issues a one-year client certificate and registers its serial as `active`.
3. Rotation: enroll a new CSR, switch the Jetson to the new certificate, then `device_pki.py revoke <tenant> <old-serial>`. Revocation takes effect on the next request.
