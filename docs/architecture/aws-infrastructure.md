# Symphony AWS infrastructure

This slide-scale diagram is derived from every Terraform root under
`cloud/terraform/` and the architecture documents in `cloud/PLAN.md` and
`docs/superpowers/specs/`. It represents the configured `demo` pilot. Solid
arrows are request or data flows; dotted arrows are governance relationships.

For the inventory of what was actually deployed, how the pieces worked
together on a request, and the teardown record, see
[`aws-environment.md`](aws-environment.md).

```mermaid
flowchart TB
  classDef account fill:#eff6ff,stroke:#2563eb,color:#172554
  classDef service fill:#fff7e6,stroke:#d97706,color:#422006
  classDef edge fill:#ecfdf5,stroke:#059669,color:#064e3b
  classDef security fill:#f3e8ff,stroke:#7e22ce,color:#3b0764

  User[Customer user] --> Jetson[Jetson<br/>local inference]:::edge

  subgraph ORG[Symphony AWS Organization]
    direction TB
    Management[Management account<br/>Organizations · Control Tower · Identity Center<br/>Route 53 · encrypted Terraform state]:::security
    Security[Security accounts<br/>Log archive · Audit<br/>GuardDuty · Security Hub · alerting]:::security
    Shared[Shared-services account<br/>encrypted build artifacts]:::account

    subgraph TENANT[Isolated tenant account · demo · us-east-1]
      direction LR
      API[Secure cloud ingress<br/>API Gateway + WAF<br/>mTLS + Cognito]:::service
      Ingress[Streaming Lambda<br/>policy + idempotency]:::service
      Agent[AgentCore Harness<br/>memory + gateway]:::service
      Tools[Approved tools<br/>documents · knowledge base<br/>code sandbox · browser]:::service
      Bedrock[Bedrock models<br/>Kimi default · Opus step-up]:::service
      Data[Encrypted tenant data<br/>DynamoDB · S3 Vectors<br/>recordings · logs/traces]:::account
      API --> Ingress --> Agent
      Agent --> Tools & Bedrock & Data
    end
    Management -. Control Tower baselines, SCPs, budgets .-> Security & Shared & TENANT
  end

  Jetson == HTTPS mTLS + user token ==> API

  subgraph CUSTOMER[Customer-owned / fixture AWS account]
    Docs[Encrypted S3 documents<br/>approved-docs/ only]:::account
  end
  Tools == least-privilege cross-account role ==> Docs
  Tools --> Data
  Security -. findings and audit metadata .-> Management
```

## Notes

- Terraform is separated into bootstrap, landing-zone, organization,
  shared-services, tenant baseline/workload, sandbox fixture, and external
  connector roots. Remote state passes only declared outputs between roots.
- Control Tower owns its generated baseline resources; this Terraform declares
  the landing-zone configuration, organization additions, and explicit
  member-account resources without importing or duplicating Control Tower
  resources.
- All tenant application data stays in the tenant account. The fixture account
  remains the source of the sample documents; only the approved prefix may be
  read or indexed. Management, audit, log archive, and shared services do not
  host tenant prompts or connector credentials.
- The diagram excludes the planned Snowflake frontier route because no
  Snowflake Terraform resources are present. Local-only prompts remain on the
  Jetson and never traverse the cloud ingress.
