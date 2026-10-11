# tenant-workload

Phase 3 placeholder for the tenant's private-cloud request path: Regional API Gateway custom domain with mTLS, device and user authorizer, AgentCore Harness and Gateway, the Lambda tool adapter, IAM roles, log groups, and alarms.

| Item | Value |
| --- | --- |
| Account / Region | One tenant account via `AWSControlTowerExecution`; tenant `workload_region` |
| State key | `symphony/tenants/<tenant_id>/workload.tfstate` |
| Apply order | After `tenant-baseline` for the same tenant |
| Inputs | `tenant_id`, `tenant_account_id`, `workload_region`, `state_bucket` |
| Current resources | None besides the account guard. No cost. |

Do not add resources here until the Phase 3 plan is written and Gate 1 has passed.
