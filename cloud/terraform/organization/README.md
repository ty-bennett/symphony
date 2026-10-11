# organization

Builds the governed organization around the landing zone: workload OUs and their Control Tower registration, SCPs, account vending, delegated GuardDuty and Security Hub with finding alerts, Identity Center access, and tenant budgets.

| Item | Value |
| --- | --- |
| Account / Region | Management account `us-east-1`; `aws.audit` provider assumes `AWSControlTowerExecution` in the audit account; security services in `us-east-1` and `us-east-2` |
| State key | `symphony/organization.tfstate` |
| Apply order | After `landing-zone` (the guard refuses to plan until `landing_zone_arn` exists). Reads bootstrap and landing-zone state. |
| Prerequisites | Healthy landing zone; values discovered in runbook step 5 (baseline ARNs and version, portfolio ID, Identity Center instance) |

## Inputs

| Variable | Notes |
| --- | --- |
| `management_account_id`, `audit_account_id`, `state_bucket` | Must match bootstrap state |
| `controltower_baseline_arn`, `controltower_baseline_version`, `identity_center_enabled_baseline_arn` | From `aws controltower list-baselines` / `list-enabled-baselines` |
| `account_factory_portfolio_id`, `account_factory_principal`, `account_factory_sso_user` | Account Factory access and the initial SSO user per vended account |
| `shared_services_account`, `fixture_account`, `tenants` | Every account email must be unique; tenant Regions limited to `us-east-1`/`us-east-2` |
| `security_contacts`, `operations_contacts` | Required, non-empty |
| `identity_center_instance_arn`, `identity_store_id`, `identity_users` | Users may join only known groups |
| `budget_delivery_test_amount_usd` | Optional; creates a temporary fixture budget to prove alert delivery |

## Outputs

`workload_ou_ids`, `shared_services_account_id`, `fixture_account_id`, `tenant_accounts` (ID and Region per tenant), `security_alert_topic_arns`, `permission_set_arns`, `tenant_budget_names`.

## Cost-bearing resources

GuardDuty (per account, per Region, by CloudTrail/VPC/DNS volume) and Security Hub CSPM checks (standards off, so mostly Control Tower's service-managed controls) are the main recurring costs. Two KMS keys and two SNS topics in the audit account. Each vended account adds Control Tower's Config and CloudTrail baseline cost. Budgets: the first two per account are free, then $0.02/day each.

## Notes

- AWS Budgets notify; they are not a spending cap. Defaults: 80% forecast, 80% actual, 100% actual.
- SCPs exempt only `AWSControlTowerExecution`. Each must stay at most 5,120 characters (tested).
- MFA enforcement in Identity Center has no API. Set it by hand (runbook step 6).
- Vended accounts and OUs have `prevent_destroy`; removing a tenant from `tenants` fails the plan instead of terminating the account.
- Control Tower owns the baselines' deployed resources and its own Identity Center groups and permission sets.
