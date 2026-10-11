# Symphony API contract

Source of truth for the on-prem team building the Jetson client.

- [openapi.yaml](openapi.yaml): endpoint, authentication (mTLS + Cognito bearer token), responses.
- [request.v1.schema.json](request.v1.schema.json): request body (JSON Schema 2020-12).
- [sse-events.v1.md](sse-events.v1.md): the response stream and device enrollment.
- [errors.md](errors.md): stable error codes and what the client should do.
- [examples/](examples/): requests and recorded streams.

The ingress Lambda's tests (`cloud/services/ingress/test/contract.test.mjs`) fail if its validator drifts from this contract.
Values the client needs per tenant (from `terraform output` in `cloud/terraform/tenant-workload`): `ingress_url`, `jetson_app_client_id`, and the user pool ID.
