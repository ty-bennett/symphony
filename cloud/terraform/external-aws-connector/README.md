# external-aws-connector

The customer-side half of the S3 document connector: `SymphonyIntegrationRole` in the account that owns the documents. For the pilot that is the Symphony integration fixture account; a real customer reviews and applies this template in their own account, which stays outside Symphony's Organization.

| Item | Value |
| --- | --- |
| Account / Region | Document-owning account via `AWSControlTowerExecution` (pilot: fixture account), `us-east-1` |
| State key | `symphony/tenants/<tenant_id>/external-aws-connector.tfstate` |
| Apply order | After `tenant-workload` (the trusted role must exist before IAM accepts it as a principal) |
| Inputs | `fixture_account_id`, `tenant_id`, `state_bucket` |
| Outputs | `integration_role_arn` |
| Cost-bearing resources | None (IAM only) |

## Permissions

- Trust: only the exact tenant tool role, and only with the tenant's external ID (`sts:ExternalId`). The external ID is an extra condition, not a secret.
- `s3:ListBucket` on the bucket only for `approved-docs/` and below; `s3:GetObject` only under `approved-docs/`; `kms:Decrypt` on the bucket key only via S3. No write, delete, or discovery permissions.
- Revoke Symphony by deleting this role (or its trust statement); the tenant tool then fails closed.
