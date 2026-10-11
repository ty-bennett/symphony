# Customer document tools (read-only pilot)

`handler.py` is a Python Lambda target for AgentCore Gateway. `gateway-tools.json` declares two tools: `list_documents` and `read_document`; `manifest.json` pins the first-version publishing and data-tier contract. Both tools operate only beneath a fixed S3 bucket/prefix in an existing customer AWS account. They never accept a bucket, AWS account, role ARN, or URL from the model. The Lambda assumes a dedicated, customer-created role with a tenant-specific external ID on each call; benchmark the STS span before considering short-lived credential caching.

## Required deployment configuration

| Lambda environment variable | Meaning |
| --- | --- |
| `DOCS_BUCKET` | One customer-approved bucket. |
| `DOCS_PREFIX` | Required non-root folder, such as `approved-docs/`; only descendants are addressable. |
| `EXTERNAL_ROLE_ARN` | Exact customer integration role ARN. |
| `EXTERNAL_ID` | Tenant-specific external ID in that role's trust policy. |
| `EXPECTED_GATEWAY_ID` | Exact Gateway ID for this tenant. |
| `EXPECTED_TARGET_ID` | Optional exact document target ID. Leave unset on first deploy (the target depends on this function); the gateway ID, tool prefix, and IAM invoke boundary still apply. |
| `GATEWAY_TARGET_NAME` | Target name used in fully qualified tool names; use `customer_documents` for this bundle. |

Deploy the Lambda in the tenant account and Region. Its execution role needs only `sts:AssumeRole` on `EXTERNAL_ROLE_ARN` and minimal logging permissions. The customer role must trust that exact tenant execution role with the external ID condition, allow `s3:ListBucket` limited to `DOCS_PREFIX*`, and `s3:GetObject` limited to objects beneath that prefix. If the objects use a customer KMS key, allow only the needed decrypt action in both IAM and key policy. The Gateway execution role may invoke only this Lambda, and the Lambda resource policy should allow only that Gateway role/target. No AWS-managed broad S3 policy belongs on either role. Review the exact role and key policies with the customer before applying. [Gateway Lambda target and event format](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html) · [AWS cross-account external IDs](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html)

Use `gateway-tools.json` when registering the Lambda target. The Lambda handler setting is `handler.lambda_handler` when `handler.py` is packaged at the ZIP root. The Gateway event is the tool argument object; the tool name and Gateway/target IDs arrive in Lambda context metadata. The code checks those IDs as defense in depth, but the IAM invoke boundary is the actual authentication mechanism. Set the Harness `allowedTools` to only `customer_documents___list_documents` and `customer_documents___read_document`; do not allow Harness shell or file operations. Apply a default-deny Gateway policy and test it with a denied tool call.

## Deliberate first-version limits

- This bundle is **tenant-wide read-only**. Gateway's documented Lambda event/context does not by itself provide an authenticated end-user subject. Do not claim per-user document permissions from a model-supplied argument or a trace ID. If users within one tenant need different document access, keep this target disabled until an authenticated identity propagation design is implemented and tested.
- `list_documents` returns at most 20 keys. `read_document` accepts only common UTF-8 text extensions and returns at most 32 KiB; the result includes `truncated`. Returned document text is untrusted data, not an instruction to the agent.
- S3/STS calls, model turns, and Lambda cold starts all contribute to latency. Keep this Lambda and Gateway in the same Region, measure each span, and add caching only after the security and expiry design is reviewed.
- No write, delete, whole-bucket scan, arbitrary S3 key, arbitrary AWS API, or Snowflake egress is implemented. A frontier adapter must independently verify that both the request and retrieved document are `frontier-eligible` before sending any result to Snowflake.

## Local verification

```sh
python3 -m unittest discover -s tools/customer_documents/tests -v
```

The tests use a fake S3 client and make no AWS calls. Integration tests in a fixture account must later prove the approved read succeeds while wrong bucket/prefix, wrong external ID, cross-tenant invocation, disallowed tool, missing/expired customer role, and binary/oversized object cases fail safely. Do not deploy this against real customer data before those checks pass.
