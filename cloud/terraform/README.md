# Symphony Terraform foundation

Terraform for the Phase 1 AWS landing zone in [`../PLAN.md`](../PLAN.md). Each directory below is a separate root with its own state file, account guard, and README. Apply them in order with [`../runbooks/foundation-bootstrap.md`](../runbooks/foundation-bootstrap.md); nothing here has been applied until that runbook's evidence exists.

| Order | Root | Account | Region | State key |
| --- | --- | --- | --- | --- |
| 1 | [`bootstrap/`](bootstrap/README.md) | Management | us-east-1 | `symphony/bootstrap.tfstate` (local first, then migrated) |
| 2 | [`landing-zone/`](landing-zone/README.md) | Management | us-east-1 | `symphony/landing-zone.tfstate` |
| 3 | [`organization/`](organization/README.md) | Management + audit | us-east-1, us-east-2 | `symphony/organization.tfstate` |
| 4 | [`shared-services/`](shared-services/README.md) | Shared services | us-east-1 | `symphony/shared-services.tfstate` |
| 5 | [`tenant-baseline/`](tenant-baseline/README.md) | One tenant | Tenant workload Region | `symphony/tenants/<tenant_id>/baseline.tfstate` |
| 6 | [`sandbox-fixture/`](sandbox-fixture/README.md) | Integration fixture | us-east-1 | `symphony/sandbox-fixture.tfstate` |
| — | [`tenant-workload/`](tenant-workload/README.md) | One tenant | Tenant workload Region | `symphony/tenants/<tenant_id>/workload.tfstate` (Phase 3) |

Shared modules live in [`modules/`](modules/): `secure-bucket`, `ct-account`, `finding-alerts`, and `account-hygiene`.

## Rules every root follows

- Terraform `>= 1.16.0, < 2.0.0` and AWS provider `~> 6.68.0`, pinned by the committed `.terraform.lock.hcl` (darwin_arm64 and linux_amd64).
- The provider sets `allowed_account_ids`, and `terraform_data.account_guard` fails the plan when the caller account, Region, or upstream state is wrong.
- Member-account roots assume `AWSControlTowerExecution` in exactly one account. Management roots use the operator's management session.
- Control Tower owns the resources it creates (baseline StackSets, the CloudTrail trail, Config recorders, log buckets, its Identity Center groups). Terraform references them and never declares or imports them.
- IAM, KMS, and bucket policies are built with `jsonencode`, never `aws_iam_policy_document`, so tests can read them.
- Real inputs go in gitignored `terraform.tfvars` and `backend.hcl`. Committed examples and tests use repeated-digit placeholder account IDs only.

## Offline checks

```sh
cloud/terraform/scripts/check.sh
```

It runs `terraform fmt -check`, `init -backend=false -lockfile=readonly`, `validate`, and `terraform test` (mocked providers, no AWS calls) in every root and module, then `scripts/static_checks.py`. CI runs the same script.
