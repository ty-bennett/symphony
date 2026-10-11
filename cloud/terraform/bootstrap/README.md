# bootstrap

Creates what must exist before Control Tower: the Terraform state backend, the AWS Organization, the Security OU, the log archive and audit accounts, and the three Control Tower service roles.

| Item | Value |
| --- | --- |
| Account / Region | Management account, `us-east-1` |
| State key | Local for the first apply, then `symphony/bootstrap.tfstate` in the bucket this root creates |
| Apply order | First. Requires no Organization or landing zone to exist yet. |
| Prerequisites | Management account root email, payment method, MFA; unique emails for log archive and audit; operator session with administrator access in the management account |

## Inputs

| Variable | Notes |
| --- | --- |
| `management_account_id` | 12 digits; the provider and guard refuse any other account |
| `log_archive_account_email`, `audit_account_email` | Unique; must differ from each other |
| `log_archive_account_name`, `audit_account_name`, `security_ou_name` | Defaults: `Log Archive`, `Audit`, `Security` |
| `existing_organization_id` | Optional. Adopts an Organization that already exists (import) instead of creating one |

## Outputs

`management_account_id`, `organization_id`, `organization_root_id`, `security_ou_id`, `log_archive_account_id`, `audit_account_id`, `state_bucket_name`, `state_kms_key_arn`. No secrets.

## Cost-bearing resources

One KMS key (about $1/month plus requests) and one S3 bucket holding small state files. Organizations and IAM roles are free. The two accounts cost nothing until Control Tower enables logging in them.

## Ownership notes

- `aws_service_access_principals` and `enabled_policy_types` are ignored after creation, so Terraform never removes trusted access or policy types that Control Tower or the organization root enable.
- The organization, Security OU, accounts, state key, and state bucket have `prevent_destroy`. Accounts use `close_on_deletion = false`.
- `AWSControlTowerConfigAggregatorRoleForOrganizations` is not created; landing zone 4.0 does not require it.

## First apply and state migration

See steps 2–3 of [`../../runbooks/foundation-bootstrap.md`](../../runbooks/foundation-bootstrap.md). In short: create a gitignored `backend_override.tf` with `terraform { backend "local" {} }`, apply, delete the override, then `terraform init -migrate-state -backend-config=../backend.hcl -backend-config="key=symphony/bootstrap.tfstate"`.
