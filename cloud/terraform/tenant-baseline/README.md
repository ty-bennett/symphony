# tenant-baseline

Per-tenant regional baseline in that tenant's workload account: account hygiene, a tenant data KMS key, an encrypted application log group, the device certificate registry, and the pilot Cognito user pool.

| Item | Value |
| --- | --- |
| Account / Region | One tenant account via `AWSControlTowerExecution`; the tenant's `workload_region` (`us-east-1` or `us-east-2`) |
| State key | `symphony/tenants/<tenant_id>/baseline.tfstate` (one state per tenant) |
| Apply order | After `organization`. The guard checks the tenant ID, account ID, and Region against `tenant_accounts` in organization state. |
| Inputs | `tenant_id`, `tenant_account_id`, `workload_region`, `state_bucket`, `log_retention_days` (default 30) |
| Outputs | `tenant_data_kms_key_arn`, `device_registry_table_name`, `device_registry_table_arn`, `user_pool_id`, `user_pool_arn`, `application_log_group_name` |

## Resources

- **KMS key** `alias/symphony/<tenant_id>/data`: rotates; CloudWatch Logs may use it only for `/symphony/<tenant_id>/*` log groups.
- **Device registry** DynamoDB table keyed by `cert_serial`, with a `by_device_id` index so overlapping certificates coexist during rotation. On-demand, PITR, KMS encryption, deletion protection.
- **Cognito user pool**: Lite tier, admin-created users only, TOTP MFA required, 14-character passwords, email changes need verification, deletion protection. App clients come with the Phase 3 ingress.

## Cost-bearing resources

One KMS key; DynamoDB on-demand requests, storage, and PITR (negligible at pilot scale); CloudWatch Logs ingestion and storage; Cognito Lite MAUs beyond the free tier.

The key, table, and user pool have `prevent_destroy`.
