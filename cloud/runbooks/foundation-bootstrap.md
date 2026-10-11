# Foundation bootstrap runbook

Applies the Phase 1 Terraform roots in order and collects Gate 1 evidence. Record each command's result, the account and Region, and any defect in [`../evidence/phase-1.md`](../evidence/phase-1.md). Never paste credentials, tokens, or real customer data into evidence.

Stop at any step whose output differs from the expected result. Do not re-run an apply to "push through" an error without understanding it.

## 1. Preconditions

- Checklist items 0.1–0.3 are complete: `aws sts get-caller-identity` returns the intended management account, and the pilot inputs are recorded.
- Tools: Terraform 1.16.x and AWS CLI v2. Check with `terraform version` and `aws --version`.
- Use one shell with the management profile for every management-account step:

```sh
export AWS_PROFILE=symphony-management   # your profile name
export AWS_REGION=us-east-1
cloud/terraform/scripts/check.sh          # must pass before any plan
```

## 2. Read-only preflight

```sh
aws sts get-caller-identity --query Account --output text            # = management_account_id
aws sts get-caller-identity --query Arn --output text                # must NOT end in ":root"
aws organizations describe-organization                              # expect AWSOrganizationsNotInUseException
aws controltower list-landing-zones --region us-east-1               # expect "landingZones": []
aws iam get-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSControlTowerServiceRolePolicy --query Policy.Arn
aws iam get-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy --query Policy.Arn
```

If a landing zone already exists, stop. If an Organization already exists in this management account and you intend to adopt it, set `existing_organization_id` in `bootstrap/terraform.tfvars`; the bootstrap plan must then show `1 to import` and no destroys. Existing OUs and accounts stay ungoverned unless you register them later.

Run every root as an administrator IAM role or IAM user with MFA in the management account, never as the root user. STS refuses `AssumeRole` from root, so the organization root (its `aws.audit` provider) and every member-account root would fail, and the organization guard rejects a root caller. After the landing zone exists you can switch to an Identity Center `AWSAdministratorAccess` session (`aws configure sso`).

## 3. Bootstrap (local state, then migrate)

```sh
cd cloud/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars        # fill in real values
printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf
terraform init
terraform plan -out=bootstrap.tfplan
```

Review the plan: one organization, one OU, two accounts, a KMS key and alias, the state bucket and its six configuration resources, three IAM roles with their policies, and `terraform_data.account_guard`. No destroys.

```sh
terraform apply bootstrap.tfplan
terraform output                                     # record IDs in evidence

rm backend_override.tf
cp ../backend.hcl.example ../backend.hcl             # set bucket and kms_key_id from the outputs above
terraform init -migrate-state -backend-config=../backend.hcl -backend-config="key=symphony/bootstrap.tfstate"
aws s3 ls "s3://$(terraform output -raw state_bucket_name)/symphony/"   # bootstrap.tfstate is listed
terraform plan -detailed-exitcode                    # exit code 0: no changes
rm terraform.tfstate terraform.tfstate.backup bootstrap.tfplan
```

## 4. Landing zone

```sh
cd ../landing-zone
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=../backend.hcl -backend-config="key=symphony/landing-zone.tfstate"
terraform plan -out=lz.tfplan                        # 1 landing zone, 1 key, 1 alias, guard
terraform apply lz.tfplan                            # 30-60 minutes
aws controltower get-landing-zone --region us-east-1 \
  --landing-zone-identifier "$(terraform output -raw landing_zone_arn)" \
  --query 'landingZone.{status:status,drift:driftStatus.status,version:version}'
```

Expected: `ACTIVE`, `IN_SYNC`, `4.0`.

## 5. Discover organization inputs

```sh
aws controltower list-baselines --region us-east-1 --query 'baselines[].{name:name,arn:arn}'
aws controltower list-enabled-baselines --region us-east-1 \
  --query 'enabledBaselines[].{arn:arn,baseline:baselineIdentifier,status:statusSummary.status}'
aws servicecatalog list-portfolios \
  --query "PortfolioDetails[?DisplayName=='AWS Control Tower Account Factory Portfolio'].Id" --output text
aws servicecatalog describe-product --name "AWS Control Tower Account Factory" \
  --query 'ProvisioningArtifacts[].Name'
aws sso-admin list-instances --query 'Instances[0].{arn:InstanceArn,store:IdentityStoreId}'
aws sts get-caller-identity --query Arn --output text
```

- `controltower_baseline_arn`: the `AWSControlTowerBaseline` ARN.
- `controltower_baseline_version`: the AWSControlTowerBaseline version that the [baseline compatibility table](https://docs.aws.amazon.com/controltower/latest/userguide/table-of-baselines.html) lists for landing zone 4.0.
- `identity_center_enabled_baseline_arn`: the enabled baseline whose `baselineIdentifier` is the `IdentityCenterBaseline` ARN.
- If the provisioning artifact name is not `AWS Control Tower Account Factory`, stop and update the `ct-account` module default in a reviewed change.
- `account_factory_principal`: for an IAM role, use its role ARN with `type = "IAM"`. For an Identity Center session (`assumed-role/AWSReservedSSO_<set>_<suffix>`), use `arn:aws:iam:::role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_<set>_*` with `type = "IAM_PATTERN"`.

## 6. Organization

```sh
cd ../organization
$EDITOR terraform.tfvars        # every input in README.md; tests/terraform.tfvars shows the shape
terraform init -backend-config=../backend.hcl -backend-config="key=symphony/organization.tfstate"
terraform plan -out=org.tfplan
terraform apply org.tfplan                           # account vending takes 20-40 minutes per account
```

If an apply fails with a Service Catalog "operation in progress" error while accounts are vending, wait for the running provisioning to finish in the Service Catalog console, then run `terraform plan` and `apply` again.

### Recovering a failed Account Factory provisioning

If a vended account ends in `ERROR` or `TAINTED` (reused email, account quota, transient Control Tower error), Terraform marks `module.<account>.aws_servicecatalog_provisioned_product.this` tainted and the next plan fails on `prevent_destroy`. Do not remove `prevent_destroy`.

1. Inspect it: `aws servicecatalog describe-provisioned-product --name symphony-<name>` and the record's `RecordErrors` (`aws servicecatalog describe-record --id <last-record-id>`).
2. Fix the cause (for example a unique email in `terraform.tfvars`).
3. If the account was created and is `ACTIVE` in Organizations, clear the taint: `terraform untaint 'module.<account>.aws_servicecatalog_provisioned_product.this'` and re-plan; Terraform updates the product in place.
4. If no account was created, terminate the failed product in Service Catalog (`aws servicecatalog terminate-provisioned-product --provisioned-product-name symphony-<name>`), then `terraform state rm 'module.<account>.aws_servicecatalog_provisioned_product.this'` and re-plan to provision it again.

Manual steps after the apply:

1. **Identity Center MFA.** Console → IAM Identity Center → Settings → Authentication → Multi-factor authentication: prompt users "Every time they sign in", allow security keys and authenticator apps, and require users to register a device at sign-in.
2. **Finding alert subscriptions.** Every address in `security_contacts` receives two SNS confirmation emails (one per Region). Confirm them.
3. **Root users.** For each new member account, keep the root email mailbox controlled by Symphony operations. Do not create root access keys.

## 7. Member-account roots

```sh
cd ../shared-services
terraform init -backend-config=../backend.hcl -backend-config="key=symphony/shared-services.tfstate"
terraform plan -out=ss.tfplan && terraform apply ss.tfplan

cd ../tenant-baseline                                # once per tenant
terraform init -reconfigure -backend-config=../backend.hcl -backend-config="key=symphony/tenants/<tenant_id>/baseline.tfstate"
terraform plan -out=tb.tfplan && terraform apply tb.tfplan

cd ../sandbox-fixture
terraform init -backend-config=../backend.hcl -backend-config="key=symphony/sandbox-fixture.tfstate"
terraform plan -out=fx.tfplan && terraform apply fx.tfplan
```

Each root needs its own `terraform.tfvars` copied from its `terraform.tfvars.example`, with values from `terraform -chdir=../organization output`.

## 8. Gate 1 evidence

| Check | Command (expected result) |
| --- | --- |
| Landing zone healthy (1.7) | Step 4 query: `ACTIVE`, `IN_SYNC` |
| OUs registered (1.7a) | `aws controltower list-enabled-baselines --region us-east-1`: three workload OU targets `SUCCEEDED` |
| Accounts placed (1.6, 1.8–1.10) | `aws organizations list-accounts-for-parent --parent-id <ou-id>` for Security, Infrastructure, Customers, Sandbox |
| Central audit (1.11) | As a SymphonySecurityAudit user in log archive: `aws s3 ls s3://aws-controltower-logs-<log-archive-id>-us-east-1/<org-id>/AWSLogs/<tenant-account-id>/CloudTrail/ --recursive \| tail -3` lists recent objects |
| Security finding (1.12) | With audit-account credentials (assume `AWSControlTowerExecution` from management): `aws guardduty list-detectors --region us-east-1`, then `aws guardduty create-sample-findings --region us-east-1 --detector-id <id> --finding-types 'Backdoor:EC2/C&CActivity.B!DNS'`. A security contact receives the email within 15 minutes. |
| Human access (1.13) | A `tenant-<id>-readonly` test user signs in to the access portal and sees only the tenant account with `CustomerReadOnly` |
| Budget (1.14) | `aws budgets describe-notifications-for-budget --account-id <management-id> --budget-name symphony-tenant-<id>-monthly` lists three notifications. For delivery, set `budget_delivery_test_amount_usd = 1`, apply, wait for the fixture account's KMS/S3 charges to post (up to 24 hours), confirm the operations email, then set it back to `null` and apply. |
| Policy lint | For each SCP: `aws accessanalyzer validate-policy --policy-type SERVICE_CONTROL_POLICY --policy-document "$(aws organizations describe-policy --policy-id <id> --query Policy.Content --output text)"` returns no `ERROR` or `SECURITY_WARNING` findings |
| External access | In each member account and Region: `aws accessanalyzer list-findings-v2 --analyzer-arn <arn> --filter '{"status":{"eq":["ACTIVE"]}}'` returns no unexpected findings |
| No workload in management | `aws resourcegroupstaggingapi get-resources --region us-east-2` in the management account lists only Symphony foundation resources |
| Repeatability (1.15) | `terraform plan -detailed-exitcode` in every applied root exits `0` |

## Break-glass and teardown

- Root user credentials for the management account stay offline with hardware MFA. Use them only for actions that require root, and record each use in evidence.
- `prevent_destroy` protects the organization, accounts, OUs, landing zone, KMS keys, state and artifact buckets, device registry, and user pool. Removing any of them needs a reviewed change that deletes the `prevent_destroy` line, a reviewed destroy plan, and a recorded reason.
- Never delete the state bucket or its noncurrent versions while any root still uses it.
