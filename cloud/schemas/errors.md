# Error codes (v1)

Codes are stable; messages are for people and may change.

| HTTP | `code` | Meaning | Client action |
| --- | --- | --- | --- |
| 401 | `USER_UNAUTHORIZED` | Missing, expired, or wrong-pool user token, or a token from another app client. | Refresh the token or sign the user in again. |
| 403 | `DEVICE_REVOKED` | Client certificate unknown, revoked, expired, or from another tenant. | Stop sending; re-enroll the device. |
| 403 | (none: body `{"message":"Forbidden"}`) | Client certificate not signed by the tenant CA; API Gateway refuses it before any Symphony code runs. Sending no certificate at all fails the TLS connection instead. | Check the device's certificate chain; re-enroll. |
| 403 | `DATA_TIER_DENIED` | `local-only` sent to the cloud, or `step-up` without `frontier-eligible`. | Keep the prompt on the device or lower the model preference. |
| 400 | `INVALID_REQUEST` | Body does not match `request.v1.schema.json` (including any unknown field such as `tenant_id`). | Fix the request; do not retry unchanged. |
| 409 | `DUPLICATE_REQUEST` | `request_id` already used by this user in the last 24 hours. | Use a new `request_id` for a new attempt. |
| 429 | (gateway) | Stage throttle or per-IP WAF rate limit. | Retry with exponential backoff. |
| 503 | `MODEL_UNAVAILABLE` | The model is throttled or unavailable, or the agent failed. No fallback to a less private route happens. | Retry later with a new `request_id`. |
| 504 | `UPSTREAM_TIMEOUT` | The agent did not finish before `deadline_ms`. | Retry with a longer deadline or a narrower question. |
| 500 | `INTERNAL` | Unexpected failure. | Retry once with a new `request_id`; report the `request_id`. |

After the stream has started (HTTP 200), failures arrive as a final `error` event with the same codes.
