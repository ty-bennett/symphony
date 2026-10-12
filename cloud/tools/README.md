# Customer tool catalog

Keep customer-facing tool implementations here, separate from ingress, routing, and agent configuration. The first custom bundle is [`customer_documents/`](customer_documents/README.md), a read-only S3 connector for an existing customer AWS account. Deploy the code, IAM role, and Gateway target separately for each tenant; do not share credentials or a cross-tenant Lambda.

## What AWS already provides

| Capability | AWS path | Initial decision |
| --- | --- | --- |
| Current public web information | [AgentCore Web Search Tool](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html) as a Gateway connector | Use the managed connector when the customer enables web access; do not build a scraper. |
| Search over indexed internal documents | [Managed Knowledge Bases connector](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-managed-kb.html) with `Retrieve` and `AgenticRetrieveStream` | Evaluate after the pilot if indexing cost and data policy justify it. |
| Browser interaction | [AgentCore Browser](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser.html) | Off in the read-only pilot; enable only with site allowlists and approval for state changes. |
| Sandboxed code execution | [AgentCore Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter.html) | Off in the read-only pilot; never treat it as a tenant-admin shell. |
| Existing APIs and MCP servers | [Gateway OpenAPI, Smithy, Lambda, and MCP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html) | Reuse an existing API where its authorization contract is suitable; write a Lambda adapter for customer-specific access rules. |

The initial S3 tool is custom because it must assume one customer-approved role, pin the bucket and prefix in deployment configuration, reject model-supplied resource identifiers, and limit output before it re-enters a model. A direct Smithy target is not a substitute for those checks; Gateway's Smithy support also has [protocol limitations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-smithy-targets.html). Do not offer generic AWS SDK, shell, unrestricted HTTP fetch, write/delete, or arbitrary SQL tools to the first pilot.

## Publishing contract

Each tool bundle needs a reviewed schema, execution role, target account/Region, resource allowlist, data-tier rule, owner, timeout, output cap, logging policy, tests, and rollback instructions. AgentCore Gateway prefixes visible names with `<target_name>___<tool_name>`; configure the Harness `allowedTools` list with those fully qualified names. A tool implementation cannot trust a tenant, user, bucket, role ARN, or endpoint selected by the model. [Gateway naming](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html) · [Harness allowlists](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-tools.html)

Run local tests with:

```sh
python3 -m unittest discover -s tools/customer_documents/tests -v
```

No AWS resources are created by this folder. Deployment remains gated on an identified tenant account, approved Region and customer-owned integration role, IAM policy review, and the Phase 1 landing-zone checks in `docs/design/cloud/PLAN.md`.
