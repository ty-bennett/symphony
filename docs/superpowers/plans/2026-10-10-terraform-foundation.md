# Symphony Terraform Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write, offline-test, and document every Terraform root and module for the Phase 1 AWS landing zone (checklist items 1.1–1.15), ready to apply with a runbook as soon as AWS access is restored.

**Architecture:** Seven Terraform roots with separate state (bootstrap → landing-zone → organization → shared-services / tenant-baseline / sandbox-fixture, plus a guarded Phase 3 tenant-workload scaffold) and four small modules. Every root guards its target account and Region with `allowed_account_ids` plus a `terraform_data.account_guard` precondition, and reads upstream outputs through `terraform_remote_state`. Behavior is pinned by plan-mode `terraform test` files against mocked providers (no AWS calls), plus a Python static check for invariants `terraform test` cannot see (`prevent_destroy`, Control Tower `ignore_changes`, no real account IDs).

**Tech Stack:** Terraform 1.16.4, hashicorp/aws provider 6.68.0, AWS Control Tower landing zone 4.0, AWS Organizations, Account Factory (Service Catalog), IAM Identity Center, GuardDuty, Security Hub CSPM, AWS Budgets, KMS, S3, DynamoDB, Cognito, Python 3 (static checks), GitHub Actions.

**Spec:** `cloud/PLAN.md` (architecture and acceptance) and `cloud/IMPLEMENTATION_CHECKLIST.md` (Phase 1 items 1.1–1.15). Before starting, read these PLAN.md sections: "Decisions for the first release", "AWS foundation and Terraform ownership", "Security, logging, and spend controls", and "Repository deliverables for implementation".

**Out of scope:** any `terraform apply` or AWS API call; Phase 2 (Jetson); Phase 3 workload resources (API Gateway, AgentCore, Lambda target, customer connector role); Snowflake. The runbook written in Task 13 is how a human applies this later.

**Verification status:** every file below was built and run in a scratch copy before the plan was written: `cloud/terraform/scripts/check.sh` passed with 78 tests across 11 directories and 0 static-check violations.

## Global Constraints

- Run all commands from the repository root (`/Users/tybennett/Developer/hackathon/symphony`). Work on the current `cloud` branch. Stage only the paths each task names; `cloud/PLAN.md`, `cloud/IMPLEMENTATION_CHECKLIST.md`, `cloud/tools/`, `docs/` images, and `sebastian/` are the user's files.
- Never run `terraform apply`, a backend-initialized `terraform plan`, or any AWS CLI command while implementing this plan. Tests use `mock_provider` only.
- Terraform `required_version = ">= 1.16.0, < 2.0.0"`; AWS provider `version = "~> 6.68.0"`; commit every `.terraform.lock.hcl`, generated with `terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`. `check.sh` uses `-lockfile=readonly`, so a missing or stale lock file fails.
- Control Tower home Region `us-east-1`; governed Regions exactly `us-east-1` and `us-east-2`; tenant workload Region `us-east-1` or `us-east-2`. Landing zone version `4.0`: the manifest has no `organizationStructure`, and every integration has an explicit `enabled` flag.
- Separate state per root, with keys `symphony/bootstrap.tfstate`, `symphony/landing-zone.tfstate`, `symphony/organization.tfstate`, `symphony/shared-services.tfstate`, `symphony/sandbox-fixture.tfstate`, `symphony/tenants/<tenant_id>/baseline.tfstate`, and `symphony/tenants/<tenant_id>/workload.tfstate`.
- Each root has an explicit expected account ID (`allowed_account_ids` + guard). Member-account roots assume `arn:aws:iam::<account>:role/AWSControlTowerExecution`. A plan must fail rather than target the wrong account or Region.
- Control Tower owns the baseline resources and OUs it creates: reference them, never declare or import them. Never remove trusted access or policy types that Control Tower enables.
- `prevent_destroy` on the state bucket, every KMS key except the fixture key, the organization, OUs, the landing zone, and every account (accounts also get `close_on_deletion = false`).
- No real account IDs, access keys, private keys, certificates, state, plans, or customer documents in Git. Placeholder account IDs are one digit repeated 12 times (`111111111111`). Real inputs go in gitignored `terraform.tfvars` and `backend.hcl`.
- No IAM users or access keys; use IAM Identity Center only. No Site-to-Site VPN, Tailscale, Kubernetes, service mesh, or NAT gateway.
- Build every IAM, KMS, S3, and SNS policy with `jsonencode(...)`. Never use the `aws_iam_policy_document` data source: mocks return random JSON for it, so tests could not inspect policies.
- Budgets notify at 80% forecast, 80% actual, and 100% actual of the tenant's monthly amount, to the customer billing contact and Symphony operations. Never describe them as a hard cap.
- Every Terraform root has a README stating account/Region, prerequisites, inputs, outputs, state key, apply order, expected cost-bearing resources, and what Control Tower owns.

### How the tests work (read once)

- Tests live in `<dir>/tests/*.tftest.hcl` and run with `terraform test` after `terraform init -backend=false`. Every `run` uses `command = plan`.
- `mock_provider "aws" {}` replaces the real provider. A root with an aliased provider also needs `mock_provider "aws" { alias = "audit" }`.
- Data sources are faked with `override_data`. A `run`-level `override_data` replaces a file-level one for the same target; the wrong-account tests rely on this.
- Computed attributes are unknown during plan. To assert on one, give it a value with `override_resource { ... override_during = plan }`, or set `override_during = plan` on the `mock_provider`.
- Assertions can read root resources, root outputs, and module **outputs**, but never resources inside a module. `expect_failures` can target only root-level resources, variables, outputs, and checks. That is why each root has its own `guard.tf` instead of a shared guard module.
- Shared inputs for a root's test files live in `<root>/tests/terraform.tfvars`. Terraform test loads that file automatically, and the `.gitignore` exception in Task 1 keeps it committed.

## Review Focus

1. **The same email reused for two accounts** (including different capitalization). Account Factory fails 20+ minutes into an apply and leaves a half-vended account. Expected: the plan rejects it up front. Pinned by `rejects_reused_account_emails` (Task 2) and `rejects_a_reused_account_email` (Task 6).
2. **An SCP that grows past 5,120 characters** when someone adds an action. AWS rejects it only at apply time. Expected: the test suite fails first. Pinned by `fits_the_scp_size_limit` (Task 5).
3. **An empty recipient list for security alerts or budgets.** Notifications silently reach nobody. Expected: a validation error. Pinned by `rejects_an_empty_contact_list` (Task 7) and `rejects_a_tenant_without_billing_contacts` (Task 9).
4. **An Identity Center user added to a group for a tenant that does not exist** (a typo in `tenant-<id>-admins`). The apply fails halfway, or the user silently gets no access. Expected: a validation error naming the allowed groups. Pinned by `rejects_a_group_for_an_unknown_tenant` (Task 8).
5. **A later edit that drops `prevent_destroy`, removes the Control Tower `ignore_changes`, or commits a real account ID.** The next apply would destroy an account or key, strip Control Tower's trusted access, or leak an ID into Git. Expected: `check.sh` fails. Pinned by the `scripts/static_checks.py` entries added in each task, with the negative check proven in Task 1.

---

### Task 1: Workspace, check scripts, CI, and the secure-bucket module

**Files:**
- Modify: `cloud/.gitignore`
- Create: `cloud/terraform/README.md`, `cloud/terraform/backend.hcl.example`
- Create: `cloud/terraform/scripts/check.sh`, `cloud/terraform/scripts/static_checks.py`
- Create: `.github/workflows/terraform-offline.yml`
- Create: `cloud/terraform/modules/secure-bucket/{versions.tf,variables.tf,main.tf,outputs.tf,.terraform.lock.hcl}`
- Test: `cloud/terraform/modules/secure-bucket/tests/secure_bucket.tftest.hcl`

**Interfaces:**
- Consumes: nothing.
- Produces: module `modules/secure-bucket` with inputs `name` (string, S3 naming rules), `kms_key_arn` (KMS key ARN), `noncurrent_version_expiration_days` (number, default 90), and `additional_policy_statements` (any, default `[]`, appended after a `DenyInsecureTransport` statement); outputs `name` and `arn`. The bucket has `prevent_destroy`. `scripts/check.sh` checks every root and module. `scripts/static_checks.py` exposes `REQUIRED`, a list of `(file, regex, minimum_count, reason)` tuples, and `ROOTS`, the root dirs whose `guard.tf` must declare `terraform_data.account_guard`. Later tasks append to both.

- [ ] **Step 1: Ignore local-only Terraform inputs**

Append to `cloud/.gitignore`:

```gitignore
# Terraform local-only inputs and overrides (shared test inputs stay committed)
*.tfvars
!terraform/*/tests/terraform.tfvars
backend.hcl
*_override.tf
crash.log
```

- [ ] **Step 2: Write the failing module test**

Create `cloud/terraform/modules/secure-bucket/tests/secure_bucket.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  name        = "symphony-test-bucket"
  kms_key_arn = "arn:aws:kms:us-east-1:111111111111:key/00000000-0000-0000-0000-000000000000"
}

run "encrypts_with_the_customer_key" {
  command = plan

  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).kms_master_key_id == var.kms_key_arn
    error_message = "Default encryption must use the supplied KMS key."
  }

  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms"
    error_message = "Default encryption must be SSE-KMS."
  }
}

run "blocks_all_public_access" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.this.block_public_acls,
      aws_s3_bucket_public_access_block.this.block_public_policy,
      aws_s3_bucket_public_access_block.this.ignore_public_acls,
      aws_s3_bucket_public_access_block.this.restrict_public_buckets,
    ])
    error_message = "All four public access block settings must be true."
  }
}

run "enables_versioning" {
  command = plan

  assert {
    condition     = one(aws_s3_bucket_versioning.this.versioning_configuration).status == "Enabled"
    error_message = "Versioning must be enabled."
  }
}

run "denies_insecure_transport" {
  command = plan

  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Sid == "DenyInsecureTransport"
    error_message = "The first policy statement must deny non-TLS access."
  }

  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "The TLS deny must match aws:SecureTransport = false."
  }

  assert {
    condition     = contains(jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Resource, "arn:aws:s3:::symphony-test-bucket/*")
    error_message = "The TLS deny must cover objects."
  }
}

run "appends_additional_statements" {
  command = plan

  variables {
    additional_policy_statements = [{
      Sid       = "DenyOutsideAccount"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = "arn:aws:s3:::symphony-test-bucket/*"
      Condition = { StringNotEquals = { "aws:PrincipalAccount" = "111111111111" } }
    }]
  }

  assert {
    condition     = length(jsondecode(aws_s3_bucket_policy.this.policy).Statement) == 2
    error_message = "Additional statements must follow the TLS deny."
  }

  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[1].Sid == "DenyOutsideAccount"
    error_message = "The additional statement must be preserved."
  }
}

run "rejects_an_invalid_bucket_name" {
  command = plan

  variables {
    name = "Bad_Bucket_Name"
  }

  expect_failures = [var.name]
}
```

- [ ] **Step 3: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/modules/secure-bucket init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/secure-bucket test
```

Expected: FAIL with errors such as `Reference to undeclared input variable` and `Reference to undeclared resource`. The module has no configuration yet.

- [ ] **Step 4: Implement the module**

Create `cloud/terraform/modules/secure-bucket/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68.0"
    }
  }
}
```

Create `cloud/terraform/modules/secure-bucket/variables.tf`:

```hcl
variable "name" {
  description = "Globally unique S3 bucket name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.name))
    error_message = "Bucket name must be 3-63 lowercase letters, digits, dots, or hyphens."
  }
}

variable "kms_key_arn" {
  description = "Customer managed KMS key ARN used for default bucket encryption."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a KMS key ARN."
  }
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep noncurrent object versions."
  type        = number
  default     = 90

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be at least 1."
  }
}

variable "additional_policy_statements" {
  description = "Extra bucket policy statements appended after the TLS-only deny."
  type        = any
  default     = []
}
```

Create `cloud/terraform/modules/secure-bucket/main.tf`:

```hcl
locals {
  bucket_arn = "arn:aws:s3:::${var.name}"
}

resource "aws_s3_bucket" "this" {
  bucket        = var.name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.bucket_arn, "${local.bucket_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ], var.additional_policy_statements)
  })

  depends_on = [aws_s3_bucket_public_access_block.this]
}
```

Create `cloud/terraform/modules/secure-bucket/outputs.tf`:

```hcl
output "name" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.bucket
}

output "arn" {
  description = "Bucket ARN."
  value       = local.bucket_arn
}
```

- [ ] **Step 5: Run the module tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/modules/secure-bucket init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/secure-bucket providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/modules/secure-bucket validate
terraform -chdir=cloud/terraform/modules/secure-bucket test
```

Expected: `Success! The configuration is valid.` and `Success! 6 passed, 0 failed.`

- [ ] **Step 6: Add the check scripts**

Create `cloud/terraform/scripts/check.sh`:

```bash
#!/usr/bin/env bash
# Offline checks for every Terraform root and module under cloud/terraform.
# Makes no AWS calls: providers are mocked in tests and backends are skipped.
set -euo pipefail

cd "$(dirname "$0")/.."

terraform fmt -check -recursive

dirs=()
for dir in modules/*/ */; do
  dir="${dir%/}"
  case "$dir" in
    modules | scripts) continue ;;
  esac
  compgen -G "$dir/*.tf" >/dev/null || continue
  dirs+=("$dir")
done

for dir in "${dirs[@]}"; do
  echo "==> $dir"
  terraform -chdir="$dir" init -backend=false -input=false -lockfile=readonly >/dev/null
  terraform -chdir="$dir" validate -no-color
  if compgen -G "$dir/tests/*.tftest.hcl" >/dev/null; then
    terraform -chdir="$dir" test -no-color
  fi
done

python3 scripts/static_checks.py
```

Create `cloud/terraform/scripts/static_checks.py`:

```python
#!/usr/bin/env python3
"""Offline static checks for cloud/terraform that terraform test cannot express.

Exits non-zero and prints every violation. Makes no network or AWS calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
PLACEHOLDER_ACCOUNT = re.compile(r"^([0-9])\1{11}$")
SCANNED_SUFFIXES = (".tf", ".tftest.hcl", ".tfvars.example", ".hcl.example")

FORBIDDEN = {
    "aws_iam_policy_document": "Build IAM policies with jsonencode so tests can inspect them.",
    'resource "aws_iam_user"': "Use IAM Identity Center; do not create IAM users.",
    'resource "aws_iam_access_key"': "Do not create long-lived access keys.",
}

PREVENT_DESTROY = r"prevent_destroy\s*=\s*true"

# (file relative to cloud/terraform, required regex, minimum matches, reason)
REQUIRED: list[tuple[str, str, int, str]] = [
    ("modules/secure-bucket/main.tf", PREVENT_DESTROY, 1, "Buckets must not be destroyed by Terraform."),
]

# Every root must refuse to plan against the wrong account or Region.
ROOTS: list[str] = []


def scanned_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".terraform" not in path.parts
        and (
            path.name.endswith(SCANNED_SUFFIXES)
            or (path.name == "terraform.tfvars" and path.parent.name == "tests")
        )
    )


def main() -> int:
    errors: list[str] = []
    for path in scanned_files():
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for match in ACCOUNT_ID.finditer(text):
            if not PLACEHOLDER_ACCOUNT.match(match.group(0)):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: real-looking AWS account ID; use a variable or remote state")
        for needle, reason in FORBIDDEN.items():
            if needle in text:
                errors.append(f"{rel}: contains {needle!r}. {reason}")
    for rel, pattern, minimum, reason in REQUIRED:
        path = ROOT / rel
        found = len(re.findall(pattern, path.read_text(encoding="utf-8"))) if path.is_file() else 0
        if found < minimum:
            errors.append(f"{rel}: found {found} of /{pattern}/, need {minimum}. {reason}")
    for root in ROOTS:
        guard = ROOT / root / "guard.tf"
        if not guard.is_file() or 'resource "terraform_data" "account_guard"' not in guard.read_text(encoding="utf-8"):
            errors.append(f"{root}/guard.tf: missing terraform_data.account_guard.")
    for error in errors:
        print(error, file=sys.stderr)
    print(f"static checks: {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

```bash
chmod +x cloud/terraform/scripts/check.sh cloud/terraform/scripts/static_checks.py
```

- [ ] **Step 7: Prove the static check catches a violation**

```bash
echo '# 123456789012' >> cloud/terraform/modules/secure-bucket/outputs.tf
python3 cloud/terraform/scripts/static_checks.py; echo "exit=$?"
```

Expected: `modules/secure-bucket/outputs.tf:<line>: real-looking AWS account ID; use a variable or remote state`, `static checks: 1 violation(s)`, and `exit=1`. Delete the line you just appended, then run `python3 cloud/terraform/scripts/static_checks.py`. Expected: `static checks: 0 violation(s)`.

- [ ] **Step 8: Add the docs, the backend example, and CI**

Create `cloud/terraform/README.md`:

````markdown
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
````

Create `cloud/terraform/backend.hcl.example`:

```hcl
# Copy to backend.hcl (gitignored) after the bootstrap apply and fill in its outputs.
# Each root passes its own state key at init: -backend-config="key=symphony/<root>.tfstate"
bucket       = "symphony-tfstate-111111111111"
region       = "us-east-1"
encrypt      = true
kms_key_id   = "arn:aws:kms:us-east-1:111111111111:key/00000000-0000-0000-0000-000000000000"
use_lockfile = true
```

Create `.github/workflows/terraform-offline.yml`:

```yaml
name: terraform-offline

on:
  pull_request:
    paths:
      - "cloud/terraform/**"
      - ".github/workflows/terraform-offline.yml"
  push:
    branches: [main]
    paths:
      - "cloud/terraform/**"
      - ".github/workflows/terraform-offline.yml"

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.16.4
          terraform_wrapper: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Offline Terraform checks (no AWS credentials)
        run: cloud/terraform/scripts/check.sh
```

- [ ] **Step 9: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 10: Commit**

```bash
git add cloud/.gitignore cloud/terraform/README.md cloud/terraform/backend.hcl.example cloud/terraform/scripts cloud/terraform/modules/secure-bucket .github/workflows/terraform-offline.yml
git commit -m "feat(terraform): add offline check tooling and secure-bucket module"
```

---

### Task 2: bootstrap root (state backend, Organization, Security OU, shared accounts, Control Tower roles)

**Files:**
- Create: `cloud/terraform/bootstrap/{versions.tf,backend.tf,providers.tf,guard.tf,variables.tf,state.tf,organization.tf,controltower_roles.tf,outputs.tf,terraform.tfvars.example,README.md,.terraform.lock.hcl}`
- Test: `cloud/terraform/bootstrap/tests/{guard,state_backend,organization}.tftest.hcl`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: `modules/secure-bucket` (Task 1).
- Produces outputs that later roots read from `symphony/bootstrap.tfstate`: `management_account_id`, `organization_id`, `organization_root_id`, `security_ou_id`, `log_archive_account_id`, `audit_account_id`, `state_bucket_name` (`symphony-tfstate-<management_account_id>`), and `state_kms_key_arn`.

Why these resources: before `CreateLandingZone`, landing zone 4.0 requires the Organization, the log archive and audit accounts in one OU directly under the root, and the service roles `AWSControlTowerAdmin`, `AWSControlTowerCloudTrailRole`, and `AWSControlTowerStackSetRole` under `/service-role/`. Version 4.0 does not require `AWSControlTowerConfigAggregatorRoleForOrganizations`. The CloudTrail role uses the AWS managed policy `service-role/AWSControlTowerCloudTrailRolePolicy`, which replaced the old inline policy.

- [ ] **Step 1: Write the failing tests**

Create `cloud/terraform/bootstrap/tests/guard.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  management_account_id     = "111111111111"
  log_archive_account_email = "aws+log-archive@example.com"
  audit_account_email       = "aws+audit@example.com"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

run "plans_in_the_management_account" {
  command = plan
}

run "refuses_a_different_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "999999999999" }
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_a_different_region" {
  command = plan

  override_data {
    target = data.aws_region.current
    values = { region = "us-west-2" }
  }

  expect_failures = [terraform_data.account_guard]
}

run "rejects_a_malformed_account_id" {
  command = plan

  variables {
    management_account_id = "12345"
  }

  expect_failures = [var.management_account_id]
}
```

Create `cloud/terraform/bootstrap/tests/state_backend.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  management_account_id     = "111111111111"
  log_archive_account_email = "aws+log-archive@example.com"
  audit_account_email       = "aws+audit@example.com"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

run "names_the_state_bucket_after_the_management_account" {
  command = plan

  assert {
    condition     = module.state_bucket.name == "symphony-tfstate-111111111111"
    error_message = "State bucket name must be derived from the management account ID."
  }
}

run "rotates_the_state_key" {
  command = plan

  assert {
    condition     = aws_kms_key.state.enable_key_rotation
    error_message = "The state KMS key must rotate."
  }

  assert {
    condition     = jsondecode(aws_kms_key.state.policy).Statement[0].Principal.AWS == "arn:aws:iam::111111111111:root"
    error_message = "Only the management account may administer the state key."
  }
}
```

Create `cloud/terraform/bootstrap/tests/organization.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  management_account_id     = "111111111111"
  log_archive_account_email = "aws+log-archive@example.com"
  audit_account_email       = "aws+audit@example.com"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_resource {
  target          = aws_organizations_organization.this
  override_during = plan
  values = {
    id    = "o-exampleorg1"
    roots = [{ id = "r-abcd", arn = "arn:aws:organizations::111111111111:root/o-exampleorg1/r-abcd", name = "Root", policy_types = [] }]
  }
}

override_resource {
  target          = aws_organizations_organizational_unit.security
  override_during = plan
  values          = { id = "ou-abcd-security" }
}

run "creates_an_all_features_organization" {
  command = plan

  assert {
    condition     = aws_organizations_organization.this.feature_set == "ALL"
    error_message = "Control Tower requires all features."
  }
}

run "places_shared_accounts_in_the_security_ou" {
  command = plan

  assert {
    condition     = aws_organizations_organizational_unit.security.parent_id == "r-abcd"
    error_message = "The Security OU must sit directly under the root."
  }

  assert {
    condition     = aws_organizations_account.log_archive.parent_id == "ou-abcd-security" && aws_organizations_account.audit.parent_id == "ou-abcd-security"
    error_message = "Both service integration accounts must be in the Security OU."
  }

  assert {
    condition     = !aws_organizations_account.log_archive.close_on_deletion && !aws_organizations_account.audit.close_on_deletion
    error_message = "Removing an account from Terraform must never close it."
  }
}

run "creates_controltower_service_roles" {
  command = plan

  assert {
    condition     = toset(keys(aws_iam_role.controltower)) == toset(["AWSControlTowerAdmin", "AWSControlTowerCloudTrailRole", "AWSControlTowerStackSetRole"])
    error_message = "Exactly the three landing zone 4.0 service roles are required."
  }

  assert {
    condition     = alltrue([for role in aws_iam_role.controltower : role.path == "/service-role/"])
    error_message = "Control Tower service roles must use the /service-role/ path."
  }

  assert {
    condition     = jsondecode(aws_iam_role.controltower["AWSControlTowerAdmin"].assume_role_policy).Statement[0].Principal.Service == "controltower.amazonaws.com"
    error_message = "AWSControlTowerAdmin must trust only Control Tower."
  }

  assert {
    condition     = aws_iam_role_policy_attachment.controltower["AWSControlTowerCloudTrailRole/AWSControlTowerCloudTrailRolePolicy"].policy_arn == "arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy"
    error_message = "The CloudTrail role must use the AWS managed policy."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.controltower_stackset.policy).Statement[0].Resource == "arn:aws:iam::*:role/AWSControlTowerExecution"
    error_message = "The StackSet role may assume only AWSControlTowerExecution."
  }
}

run "rejects_reused_account_emails" {
  command = plan

  variables {
    audit_account_email = "AWS+Log-Archive@example.com"
  }

  expect_failures = [var.audit_account_email]
}
```

- [ ] **Step 2: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("bootstrap/state.tf", PREVENT_DESTROY, 1, "The state KMS key must not be destroyed."),
    ("bootstrap/organization.tf", PREVENT_DESTROY, 4, "Organization, Security OU, and shared accounts must not be destroyed."),
    (
        "bootstrap/organization.tf",
        r"ignore_changes\s*=\s*\[aws_service_access_principals,\s*enabled_policy_types\]",
        1,
        "Terraform must not remove trusted access or policy types that Control Tower enables.",
    ),
```

Add `"bootstrap",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/bootstrap init -backend=false -input=false
terraform -chdir=cloud/terraform/bootstrap test
python3 cloud/terraform/scripts/static_checks.py
```

Expected: `terraform test` FAILS with undeclared variable/resource errors. Static checks report violations for `bootstrap/state.tf` and `bootstrap/organization.tf` (`found 0 of ...`), plus `bootstrap/guard.tf: missing terraform_data.account_guard.`

- [ ] **Step 4: Implement the root**

Create `cloud/terraform/bootstrap/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68.0"
    }
  }
}
```

Create `cloud/terraform/bootstrap/backend.tf`:

```hcl
# First apply only: create backend_override.tf (gitignored) containing
#   terraform { backend "local" {} }
# then migrate to S3 as described in README.md.
terraform {
  backend "s3" {}
}
```

Create `cloud/terraform/bootstrap/providers.tf`:

```hcl
locals {
  home_region = "us-east-1"
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "bootstrap"
    }
  }
}
```

Create `cloud/terraform/bootstrap/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials or Region are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.management_account_id
      error_message = "Credentials are not for the configured management account. Check AWS_PROFILE."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The bootstrap root must run in us-east-1."
    }
  }
}
```

Create `cloud/terraform/bootstrap/variables.tf`:

```hcl
variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "security_ou_name" {
  description = "Name of the OU that holds the Control Tower service integration accounts."
  type        = string
  default     = "Security"
}

variable "log_archive_account_name" {
  description = "Display name of the log archive account."
  type        = string
  default     = "Log Archive"
}

variable "log_archive_account_email" {
  description = "Unique root email address for the log archive account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.log_archive_account_email))
    error_message = "log_archive_account_email must be an email address."
  }
}

variable "audit_account_name" {
  description = "Display name of the audit account."
  type        = string
  default     = "Audit"
}

variable "audit_account_email" {
  description = "Unique root email address for the audit account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.audit_account_email))
    error_message = "audit_account_email must be an email address."
  }

  validation {
    condition     = lower(var.audit_account_email) != lower(var.log_archive_account_email)
    error_message = "The audit and log archive accounts need different email addresses."
  }
}
```

Create `cloud/terraform/bootstrap/state.tf`:

```hcl
locals {
  state_bucket_name = "symphony-tfstate-${var.management_account_id}"
}

resource "aws_kms_key" "state" {
  description             = "Encrypts Symphony Terraform state"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableManagementAccountIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.management_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/symphony/terraform-state"
  target_key_id = aws_kms_key.state.key_id
}

module "state_bucket" {
  source = "../modules/secure-bucket"

  name        = local.state_bucket_name
  kms_key_arn = aws_kms_key.state.arn
  additional_policy_statements = [{
    Sid       = "DenyPrincipalsOutsideManagementAccount"
    Effect    = "Deny"
    Principal = "*"
    Action    = "s3:*"
    Resource  = ["arn:aws:s3:::${local.state_bucket_name}", "arn:aws:s3:::${local.state_bucket_name}/*"]
    Condition = { StringNotEquals = { "aws:PrincipalAccount" = var.management_account_id } }
  }]
}
```

Create `cloud/terraform/bootstrap/organization.tf`:

```hcl
resource "aws_organizations_organization" "this" {
  feature_set          = "ALL"
  enabled_policy_types = ["SERVICE_CONTROL_POLICY"]

  lifecycle {
    prevent_destroy = true
    # Control Tower and the organization root enable trusted access and policy types
    # after creation. Terraform sets only the initial values and never removes theirs.
    ignore_changes = [aws_service_access_principals, enabled_policy_types]
  }
}

# Landing zone 4.0 no longer creates the Security OU; the service integration
# accounts must share one OU directly under the root before CreateLandingZone.
resource "aws_organizations_organizational_unit" "security" {
  name      = var.security_ou_name
  parent_id = aws_organizations_organization.this.roots[0].id

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_organizations_account" "log_archive" {
  name                       = var.log_archive_account_name
  email                      = var.log_archive_account_email
  parent_id                  = aws_organizations_organizational_unit.security.id
  close_on_deletion          = false
  iam_user_access_to_billing = "DENY"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [role_name, iam_user_access_to_billing]
  }
}

resource "aws_organizations_account" "audit" {
  name                       = var.audit_account_name
  email                      = var.audit_account_email
  parent_id                  = aws_organizations_organizational_unit.security.id
  close_on_deletion          = false
  iam_user_access_to_billing = "DENY"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [role_name, iam_user_access_to_billing]
  }
}
```

Create `cloud/terraform/bootstrap/controltower_roles.tf`:

```hcl
# Service roles that CreateLandingZone requires in the management account.
# AWSControlTowerConfigAggregatorRoleForOrganizations is not required for landing zone 4.0.
# Attachments are non-exclusive: Control Tower attaches
# AWSControlTowerIdentityCenterManagementPolicy itself and Terraform leaves it alone.
locals {
  controltower_roles = {
    AWSControlTowerAdmin = {
      service             = "controltower.amazonaws.com"
      managed_policy_arns = ["arn:aws:iam::aws:policy/service-role/AWSControlTowerServiceRolePolicy"]
    }
    AWSControlTowerCloudTrailRole = {
      service             = "cloudtrail.amazonaws.com"
      managed_policy_arns = ["arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy"]
    }
    AWSControlTowerStackSetRole = {
      service             = "cloudformation.amazonaws.com"
      managed_policy_arns = []
    }
  }

  controltower_role_policy_attachments = {
    for pair in flatten([
      for role, config in local.controltower_roles : [
        for arn in config.managed_policy_arns : { key = "${role}/${basename(arn)}", role = role, arn = arn }
      ]
    ]) : pair.key => pair
  }
}

resource "aws_iam_role" "controltower" {
  for_each = local.controltower_roles

  name = each.key
  path = "/service-role/"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = each.value.service }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "controltower" {
  for_each = local.controltower_role_policy_attachments

  role       = aws_iam_role.controltower[each.value.role].name
  policy_arn = each.value.arn
}

resource "aws_iam_role_policy" "controltower_admin" {
  name = "AWSControlTowerAdminPolicy"
  role = aws_iam_role.controltower["AWSControlTowerAdmin"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ec2:DescribeAvailabilityZones"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "controltower_stackset" {
  name = "AWSControlTowerStackSetRolePolicy"
  role = aws_iam_role.controltower["AWSControlTowerStackSetRole"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = "arn:aws:iam::*:role/AWSControlTowerExecution"
    }]
  })
}
```

Create `cloud/terraform/bootstrap/outputs.tf`:

```hcl
output "management_account_id" {
  description = "Symphony management account ID."
  value       = var.management_account_id
}

output "organization_id" {
  description = "AWS Organizations ID."
  value       = aws_organizations_organization.this.id
}

output "organization_root_id" {
  description = "Root ID that workload OUs are created under."
  value       = aws_organizations_organization.this.roots[0].id
}

output "security_ou_id" {
  description = "Security OU holding the log archive and audit accounts."
  value       = aws_organizations_organizational_unit.security.id
}

output "log_archive_account_id" {
  description = "Log archive account ID."
  value       = aws_organizations_account.log_archive.id
}

output "audit_account_id" {
  description = "Audit (security tooling) account ID."
  value       = aws_organizations_account.audit.id
}

output "state_bucket_name" {
  description = "S3 bucket holding Terraform state for every root."
  value       = module.state_bucket.name
}

output "state_kms_key_arn" {
  description = "KMS key that encrypts Terraform state."
  value       = aws_kms_key.state.arn
}
```

Create `cloud/terraform/bootstrap/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars (gitignored) and replace every value.
management_account_id     = "111111111111"
log_archive_account_email = "aws+log-archive@example.com"
audit_account_email       = "aws+audit@example.com"
```

Create `cloud/terraform/bootstrap/README.md`:

```markdown
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/bootstrap init -backend=false -input=false
terraform -chdir=cloud/terraform/bootstrap providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/bootstrap validate
terraform -chdir=cloud/terraform/bootstrap test
```

Expected: `Success! The configuration is valid.` and `Success! 10 passed, 0 failed.`

- [ ] **Step 6: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 7: Commit**

```bash
git add cloud/terraform/bootstrap cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add bootstrap root for organization, shared accounts, and state"
```

---

### Task 3: landing-zone root (Control Tower 4.0 manifest and log key)

**Files:**
- Create: `cloud/terraform/landing-zone/{versions.tf,providers.tf,guard.tf,remote_state.tf,variables.tf,kms.tf,landing_zone.tf,outputs.tf,terraform.tfvars.example,README.md,.terraform.lock.hcl}`
- Test: `cloud/terraform/landing-zone/tests/landing_zone.tftest.hcl`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: bootstrap outputs `management_account_id`, `log_archive_account_id`, and `audit_account_id`, read through `data.terraform_remote_state.bootstrap` (key `symphony/bootstrap.tfstate`).
- Produces: outputs `landing_zone_arn`, `landing_zone_version`, `governed_regions`, and `controltower_logs_kms_key_arn`. The organization root (Task 4) refuses to plan until `landing_zone_arn` exists. `landing-zone/versions.tf` (with `backend "s3" {}`) is reused unchanged by every later root.

Manifest facts from the Control Tower API guide for 4.0:
- The top-level keys are `accessManagement`, `backup`, `centralizedLogging`, `config`, `governedRegions`, and `securityRoles`, and every integration needs a boolean `enabled`.
- `config.accountId` is the Config aggregator account, the audit account. The AWS example uses the same ID for `securityRoles.accountId`.
- The optional `kmsKeyArn` sits under each `configurations` object.

- [ ] **Step 1: Write the failing test**

Create `cloud/terraform/landing-zone/tests/landing_zone.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  management_account_id = "111111111111"
  state_bucket          = "symphony-tfstate-111111111111"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      management_account_id  = "111111111111"
      log_archive_account_id = "222222222222"
      audit_account_id       = "333333333333"
    }
  }
}

override_resource {
  target          = aws_kms_key.controltower_logs
  override_during = plan
  values          = { arn = "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111" }
}

run "uses_landing_zone_version_4" {
  command = plan

  assert {
    condition     = aws_controltower_landing_zone.this.version == "4.0"
    error_message = "Default landing zone version must be 4.0."
  }
}

run "governs_only_the_two_pilot_regions" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).governedRegions == ["us-east-1", "us-east-2"]
    error_message = "Governed Regions must be exactly us-east-1 and us-east-2."
  }
}

run "routes_logs_and_security_to_the_shared_accounts" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.accountId == "222222222222"
    error_message = "Centralized logging must target the log archive account."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).securityRoles.accountId == "333333333333"
    error_message = "Security roles must target the audit account."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).config.accountId == "333333333333"
    error_message = "The Config aggregator must be the audit account."
  }
}

run "sets_every_enabled_flag_explicitly" {
  command = plan

  assert {
    condition = alltrue([
      jsondecode(aws_controltower_landing_zone.this.manifest_json).accessManagement.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).config.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).securityRoles.enabled,
      !jsondecode(aws_controltower_landing_zone.this.manifest_json).backup.enabled,
    ])
    error_message = "Access management, logging, Config, and security roles are on; backup is off."
  }
}

run "encrypts_logs_with_the_symphony_key" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.configurations.kmsKeyArn == "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111"
    error_message = "CloudTrail logs must use the Symphony KMS key."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).config.configurations.kmsKeyArn == "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111"
    error_message = "Config logs must use the Symphony KMS key."
  }

  assert {
    condition     = jsondecode(aws_kms_key.controltower_logs.policy).Statement[2].Condition.StringEquals["aws:SourceArn"] == "arn:aws:cloudtrail:us-east-1:111111111111:trail/aws-controltower-BaselineCloudTrail"
    error_message = "CloudTrail may use the key only for the Control Tower baseline trail."
  }
}

run "refuses_bootstrap_state_from_another_account" {
  command = plan

  override_data {
    target = data.terraform_remote_state.bootstrap
    values = {
      outputs = {
        management_account_id  = "999999999999"
        log_archive_account_id = "222222222222"
        audit_account_id       = "333333333333"
      }
    }
  }

  expect_failures = [terraform_data.account_guard]
}

run "rejects_a_3x_version" {
  command = plan

  variables {
    landing_zone_version = "3.3"
  }

  expect_failures = [var.landing_zone_version]
}
```

- [ ] **Step 2: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("landing-zone/kms.tf", PREVENT_DESTROY, 1, "The Control Tower log key must not be destroyed."),
    ("landing-zone/landing_zone.tf", PREVENT_DESTROY, 1, "The landing zone must not be destroyed."),
```

Add `"landing-zone",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/landing-zone init -backend=false -input=false
terraform -chdir=cloud/terraform/landing-zone test
python3 cloud/terraform/scripts/static_checks.py
```

Expected: the test FAILS with undeclared references, and static checks report three landing-zone violations.

- [ ] **Step 4: Implement the root**

Create `cloud/terraform/landing-zone/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68.0"
    }
  }

  backend "s3" {}
}
```

Create `cloud/terraform/landing-zone/providers.tf`:

```hcl
locals {
  home_region      = "us-east-1"
  governed_regions = ["us-east-1", "us-east-2"]
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "landing-zone"
    }
  }
}
```

Create `cloud/terraform/landing-zone/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.management_account_id
      error_message = "Credentials are not for the configured management account. Check AWS_PROFILE."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The landing zone root must run in us-east-1."
    }

    precondition {
      condition     = local.bootstrap.management_account_id == var.management_account_id
      error_message = "Bootstrap state belongs to a different management account."
    }
  }
}
```

Create `cloud/terraform/landing-zone/remote_state.tf`:

```hcl
data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/bootstrap.tfstate"
    region = "us-east-1"
  }
}

locals {
  bootstrap = data.terraform_remote_state.bootstrap.outputs
}
```

Create `cloud/terraform/landing-zone/variables.tf`:

```hcl
variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "landing_zone_version" {
  description = "Control Tower landing zone version. The manifest format below is for 4.x."
  type        = string
  default     = "4.0"

  validation {
    condition     = can(regex("^4\\.[0-9]+$", var.landing_zone_version))
    error_message = "This manifest uses the landing zone 4.x format; set a 4.x version."
  }
}

variable "log_retention_days" {
  description = "Retention for CloudTrail and Config logs in the log archive bucket."
  type        = number
  default     = 365

  validation {
    condition     = var.log_retention_days >= 1
    error_message = "log_retention_days must be at least 1."
  }
}

variable "access_log_retention_days" {
  description = "Retention for S3 access logs of the log archive bucket."
  type        = number
  default     = 365

  validation {
    condition     = var.access_log_retention_days >= 1
    error_message = "access_log_retention_days must be at least 1."
  }
}
```

Create `cloud/terraform/landing-zone/kms.tf`:

```hcl
# Key policy from the Control Tower guide for optional KMS encryption of
# CloudTrail and Config logs. It lives in the management account home Region.
resource "aws_kms_key" "controltower_logs" {
  description             = "Encrypts Control Tower CloudTrail and Config logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableManagementAccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.management_account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowConfigEncryption"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudTrailEncryption"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:aws:cloudtrail:${local.home_region}:${var.management_account_id}:trail/aws-controltower-BaselineCloudTrail"
          }
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${var.management_account_id}:trail/*"
          }
        }
      },
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "controltower_logs" {
  name          = "alias/symphony/controltower-logs"
  target_key_id = aws_kms_key.controltower_logs.key_id
}
```

Create `cloud/terraform/landing-zone/landing_zone.tf`:

```hcl
locals {
  log_bucket_configuration = {
    loggingBucket       = { retentionDays = var.log_retention_days }
    accessLoggingBucket = { retentionDays = var.access_log_retention_days }
    kmsKeyArn           = aws_kms_key.controltower_logs.arn
  }

  # Landing zone 4.0 manifest: every integration needs an explicit enabled flag.
  # The audit account holds security roles and the Config aggregator; backup is off for the pilot.
  landing_zone_manifest = {
    governedRegions  = local.governed_regions
    accessManagement = { enabled = true }
    backup           = { enabled = false }
    centralizedLogging = {
      enabled        = true
      accountId      = local.bootstrap.log_archive_account_id
      configurations = local.log_bucket_configuration
    }
    config = {
      enabled        = true
      accountId      = local.bootstrap.audit_account_id
      configurations = local.log_bucket_configuration
    }
    securityRoles = {
      enabled   = true
      accountId = local.bootstrap.audit_account_id
    }
  }
}

resource "aws_controltower_landing_zone" "this" {
  manifest_json = jsonencode(local.landing_zone_manifest)
  version       = var.landing_zone_version

  timeouts {
    create = "120m"
    update = "120m"
    delete = "120m"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}
```

Create `cloud/terraform/landing-zone/outputs.tf`:

```hcl
output "landing_zone_arn" {
  description = "Control Tower landing zone ARN."
  value       = aws_controltower_landing_zone.this.arn
}

output "landing_zone_version" {
  description = "Deployed landing zone version."
  value       = aws_controltower_landing_zone.this.version
}

output "governed_regions" {
  description = "Regions governed by Control Tower."
  value       = local.governed_regions
}

output "controltower_logs_kms_key_arn" {
  description = "KMS key encrypting Control Tower logs."
  value       = aws_kms_key.controltower_logs.arn
}
```

Create `cloud/terraform/landing-zone/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars (gitignored) and replace every value.
management_account_id = "111111111111"
state_bucket          = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/landing-zone/README.md`:

```markdown
# landing-zone

Declares the Control Tower landing zone (version 4.0 manifest) and the KMS key that encrypts its CloudTrail and Config logs.

| Item | Value |
| --- | --- |
| Account / Region | Management account, `us-east-1` (home Region) |
| Governed Regions | `us-east-1`, `us-east-2` |
| State key | `symphony/landing-zone.tfstate` |
| Apply order | After `bootstrap`. Reads `symphony/bootstrap.tfstate`. |
| Prerequisites | Bootstrap applied and migrated; both shared accounts `ACTIVE` in the Security OU |

## Inputs

`management_account_id`, `state_bucket`, `landing_zone_version` (default `4.0`; only 4.x accepted because the manifest format changed), `log_retention_days` and `access_log_retention_days` (default 365).

## Outputs

`landing_zone_arn`, `landing_zone_version`, `governed_regions`, `controltower_logs_kms_key_arn`.

## Manifest choices

- `accessManagement` on: Control Tower manages Identity Center.
- `centralizedLogging` → log archive account; `config` aggregator and `securityRoles` → audit account.
- `backup` off for the pilot to avoid AWS Backup charges.
- Both log configurations use the Symphony KMS key; its policy lets Config use it and lets CloudTrail use it only for `aws-controltower-BaselineCloudTrail`.

## Cost-bearing resources

Control Tower itself is free; it enables CloudTrail (organization trail), AWS Config recorders in every governed Region of every enrolled account, S3 log buckets, and CloudWatch Logs. Config rule evaluations and configuration items are the main recurring cost. One KMS key.

## What Control Tower owns

Everything the landing zone creates: the organization trail, Config recorders and aggregator, log archive buckets, StackSets, `AWSControlTowerExecution` roles, and its Identity Center groups and permission sets. Do not declare or import them in any root.

Creation takes 30–60 minutes. `prevent_destroy` protects both the key and the landing zone.
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/landing-zone init -backend=false -input=false
terraform -chdir=cloud/terraform/landing-zone providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/landing-zone validate
terraform -chdir=cloud/terraform/landing-zone test
```

Expected: `Success! The configuration is valid.` and `Success! 7 passed, 0 failed.`

- [ ] **Step 6: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 7: Commit**

```bash
git add cloud/terraform/landing-zone cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add Control Tower landing zone 4.0 root"
```

---

### Task 4: organization root, part 1 (guards, workload OUs, Control Tower registration)

**Files:**
- Create: `cloud/terraform/organization/{versions.tf,providers.tf,remote_state.tf,guard.tf,variables.tf,ous.tf,outputs.tf,.terraform.lock.hcl}`
- Test: `cloud/terraform/organization/tests/organization.tftest.hcl`, `cloud/terraform/organization/tests/terraform.tfvars`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: bootstrap outputs `management_account_id`, `audit_account_id`, `log_archive_account_id`, and `organization_root_id`; landing-zone output `landing_zone_arn`.
- Produces:
  - `aws_organizations_organizational_unit.workload`, keyed `infrastructure`, `customers`, and `sandbox` (used by Tasks 5 and 6).
  - `aws_controltower_baseline.workload`.
  - `local.allowed_regions = ["us-east-1", "us-east-2"]`, `local.workload_ous`, and `local.bootstrap`.
  - Provider alias `aws.audit`, which assumes `AWSControlTowerExecution` in the audit account.
  - `terraform_data.account_guard`. Later resources with no other upstream dependency `depends_on` it.
  - Output `workload_ou_ids`.

The `organization` root grows over Tasks 4–9 and has **one** test file. This task creates it with the shared header, and each later task appends a section. The header's `mock_provider` sets `override_during = plan` and gives every Account Factory product an `AccountId` output of `444444444444`, so later sections can assert on vended account IDs.

- [ ] **Step 1: Write the failing tests**

Create `cloud/terraform/organization/tests/terraform.tfvars`:

```hcl
# Shared placeholder inputs for every organization test file.
management_account_id                = "111111111111"
audit_account_id                     = "333333333333"
state_bucket                         = "symphony-tfstate-111111111111"
controltower_baseline_arn            = "arn:aws:controltower:us-east-1::baseline/17BSJV3IGJ2QSGA2"
controltower_baseline_version        = "5.0"
identity_center_enabled_baseline_arn = "arn:aws:controltower:us-east-1:111111111111:enabledbaseline/XALULM96QHI525UOC"
```

Create `cloud/terraform/organization/tests/organization.tftest.hcl`, with the shared header followed by the OU section:

```hcl
mock_provider "aws" {
  override_during = plan

  # Every vended account reports the same placeholder ID during plan.
  mock_resource "aws_servicecatalog_provisioned_product" {
    defaults = {
      outputs = [{ key = "AccountId", value = "444444444444", description = "" }]
    }
  }
}

mock_provider "aws" {
  alias           = "audit"
  override_during = plan
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_caller_identity.audit
  values = { account_id = "333333333333" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      management_account_id  = "111111111111"
      audit_account_id       = "333333333333"
      log_archive_account_id = "222222222222"
      organization_root_id   = "r-abcd"
    }
  }
}

override_data {
  target = data.terraform_remote_state.landing_zone
  values = { outputs = { landing_zone_arn = "arn:aws:controltower:us-east-1:111111111111:landingzone/EXAMPLE" } }
}

# --- ous ---
run "creates_three_workload_ous_under_the_root" {
  command = plan

  assert {
    condition     = toset([for ou in aws_organizations_organizational_unit.workload : ou.name]) == toset(["Infrastructure", "Customers", "Sandbox"])
    error_message = "Infrastructure, Customers, and Sandbox OUs are required."
  }

  assert {
    condition     = alltrue([for ou in aws_organizations_organizational_unit.workload : ou.parent_id == "r-abcd"])
    error_message = "Workload OUs must sit directly under the root."
  }
}

run "registers_every_workload_ou_with_control_tower" {
  command = plan

  assert {
    condition     = length(aws_controltower_baseline.workload) == 3
    error_message = "Each workload OU needs an AWSControlTowerBaseline."
  }

  assert {
    condition     = alltrue([for b in aws_controltower_baseline.workload : one(b.parameters).value == var.identity_center_enabled_baseline_arn])
    error_message = "Each baseline must reference the Identity Center enabled baseline."
  }
}

run "refuses_to_run_before_the_landing_zone_exists" {
  command = plan

  override_data {
    target = data.terraform_remote_state.landing_zone
    values = { outputs = {} }
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_the_wrong_audit_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.audit
    values = { account_id = "999999999999" }
  }

  expect_failures = [terraform_data.account_guard]
}
```

- [ ] **Step 2: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("organization/ous.tf", PREVENT_DESTROY, 1, "Workload OUs must not be destroyed."),
```

Add `"organization",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization test
```

Expected: FAIL with undeclared variable and resource errors.

- [ ] **Step 4: Implement guards and OUs**

Copy `cloud/terraform/landing-zone/versions.tf` to `cloud/terraform/organization/versions.tf` unchanged. Then:

Create `cloud/terraform/organization/providers.tf`:

```hcl
locals {
  home_region     = "us-east-1"
  allowed_regions = ["us-east-1", "us-east-2"]
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "organization"
    }
  }
}

# Delegated security administration runs inside the audit account.
provider "aws" {
  alias               = "audit"
  region              = local.home_region
  allowed_account_ids = [var.audit_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.audit_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-organization"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "organization"
    }
  }
}
```

Create `cloud/terraform/organization/remote_state.tf`:

```hcl
data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/bootstrap.tfstate"
    region = "us-east-1"
  }
}

data "terraform_remote_state" "landing_zone" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/landing-zone.tfstate"
    region = "us-east-1"
  }
}

locals {
  bootstrap    = data.terraform_remote_state.bootstrap.outputs
  landing_zone = data.terraform_remote_state.landing_zone.outputs
}
```

Create `cloud/terraform/organization/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_caller_identity" "audit" {
  provider = aws.audit
}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.management_account_id
      error_message = "Credentials are not for the configured management account. Check AWS_PROFILE."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The organization root must run in us-east-1."
    }

    precondition {
      condition     = data.aws_caller_identity.audit.account_id == var.audit_account_id
      error_message = "The audit provider did not reach the configured audit account."
    }

    precondition {
      condition     = local.bootstrap.management_account_id == var.management_account_id && local.bootstrap.audit_account_id == var.audit_account_id
      error_message = "Bootstrap state does not match the configured management and audit accounts."
    }

    precondition {
      condition     = try(length(local.landing_zone.landing_zone_arn) > 0, false)
      error_message = "Apply the landing-zone root before the organization root."
    }
  }
}
```

Create `cloud/terraform/organization/variables.tf`:

```hcl
variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "audit_account_id" {
  description = "AWS account ID of the audit account (from bootstrap outputs)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.audit_account_id))
    error_message = "audit_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "controltower_baseline_arn" {
  description = "ARN of the AWSControlTowerBaseline baseline (aws controltower list-baselines)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:controltower:us-east-1::baseline/[A-Z0-9]+$", var.controltower_baseline_arn))
    error_message = "controltower_baseline_arn must be a us-east-1 Control Tower baseline ARN."
  }
}

variable "controltower_baseline_version" {
  description = "AWSControlTowerBaseline version compatible with the deployed landing zone."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+$", var.controltower_baseline_version))
    error_message = "controltower_baseline_version must look like 5.0."
  }
}

variable "identity_center_enabled_baseline_arn" {
  description = "ARN of the enabled IdentityCenterBaseline (aws controltower list-enabled-baselines)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:controltower:us-east-1:[0-9]{12}:enabledbaseline/[A-Z0-9]+$", var.identity_center_enabled_baseline_arn))
    error_message = "identity_center_enabled_baseline_arn must be an enabled baseline ARN."
  }
}
```

Create `cloud/terraform/organization/ous.tf`:

```hcl
locals {
  workload_ous = {
    infrastructure = "Infrastructure"
    customers      = "Customers"
    sandbox        = "Sandbox"
  }
}

resource "aws_organizations_organizational_unit" "workload" {
  for_each = local.workload_ous

  name      = each.value
  parent_id = local.bootstrap.organization_root_id

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

# Registers each OU with Control Tower so Account Factory can vend into it and
# Control Tower controls apply. Control Tower owns the resources this deploys.
resource "aws_controltower_baseline" "workload" {
  for_each = aws_organizations_organizational_unit.workload

  baseline_identifier = var.controltower_baseline_arn
  baseline_version    = var.controltower_baseline_version
  target_identifier   = each.value.arn

  parameters {
    key   = "IdentityCenterEnabledBaselineArn"
    value = var.identity_center_enabled_baseline_arn
  }

  timeouts {
    create = "60m"
    update = "60m"
    delete = "60m"
  }
}
```

Create `cloud/terraform/organization/outputs.tf`:

```hcl
output "workload_ou_ids" {
  description = "IDs of the Infrastructure, Customers, and Sandbox OUs."
  value       = { for key, ou in aws_organizations_organizational_unit.workload : key => ou.id }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! The configuration is valid.` and `Success! 4 passed, 0 failed.`

- [ ] **Step 6: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 7: Commit**

```bash
git add cloud/terraform/organization cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add organization root with registered workload OUs"
```

---

### Task 5: organization root, part 2 (service control policies)

**Files:**
- Create: `cloud/terraform/organization/scps.tf`
- Modify: `cloud/terraform/organization/tests/organization.tftest.hcl` (append)

**Interfaces:**
- Consumes: `local.allowed_regions`, `local.workload_ous`, `aws_organizations_organizational_unit.workload`, and `terraform_data.account_guard` (Task 4).
- Produces: `aws_organizations_policy.scp`, keyed `region-guardrail` and `baseline-protection`, attached to all three workload OUs. The only exempt principal is `arn:aws:iam::*:role/AWSControlTowerExecution`. Control Tower and every member-account root in this plan use it.

The Region deny exempts global services with the `NotAction` list from the AWS Organizations example SCP "Deny access to AWS based on the requested AWS Region", plus `account:*`, `identitystore:*`, and `sso:*`. Bedrock is deliberately **not** exempt, because the spec keeps inference in-Region.

- [ ] **Step 1: Write the failing tests**

Append to `cloud/terraform/organization/tests/organization.tftest.hcl`, separated from the existing content by one blank line:

```hcl
# --- scps ---
run "denies_regions_outside_the_pilot_pair" {
  command = plan

  assert {
    condition     = jsondecode(aws_organizations_policy.scp["region-guardrail"].content).Statement[0].Condition.StringNotEquals["aws:RequestedRegion"] == ["us-east-1", "us-east-2"]
    error_message = "Only us-east-1 and us-east-2 may be allowed."
  }

  assert {
    condition     = !contains(jsondecode(aws_organizations_policy.scp["region-guardrail"].content).Statement[0].NotAction, "bedrock:*")
    error_message = "Bedrock must stay Region-restricted; it is not a global service."
  }
}

run "protects_the_security_baseline" {
  command = plan

  assert {
    condition     = toset([for s in jsondecode(aws_organizations_policy.scp["baseline-protection"].content).Statement : s.Sid]) == toset(["DenyLeavingOrganization", "DenyRootUser", "DenyLongLivedIamUserCredentials", "DenySecurityBaselineChanges", "DenyKmsKeyDeletion"])
    error_message = "Baseline protection statements are missing or unexpected."
  }
}

run "fits_the_scp_size_limit" {
  command = plan

  assert {
    condition     = alltrue([for p in aws_organizations_policy.scp : length(p.content) <= 5120])
    error_message = "Each SCP must be at most 5,120 characters or AWS rejects it at apply time."
  }
}

run "attaches_both_policies_to_every_workload_ou" {
  command = plan

  assert {
    condition     = toset(keys(aws_organizations_policy_attachment.scp)) == toset(["region-guardrail/infrastructure", "region-guardrail/customers", "region-guardrail/sandbox", "baseline-protection/infrastructure", "baseline-protection/customers", "baseline-protection/sandbox"])
    error_message = "Both SCPs must be attached to Infrastructure, Customers, and Sandbox."
  }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/organization test
```

Expected: FAIL with `Reference to undeclared resource` for `aws_organizations_policy.scp`.

- [ ] **Step 3: Implement the SCPs**

Create `cloud/terraform/organization/scps.tf`:

```hcl
locals {
  controltower_execution_role = "arn:aws:iam::*:role/AWSControlTowerExecution"

  # Global services exempt from the Region deny. Based on the AWS Organizations
  # example SCP "Deny access to AWS based on the requested AWS Region", plus
  # Identity Center and account settings used by Control Tower.
  global_service_actions = [
    "a4b:*", "account:*", "acm:*", "aws-marketplace-management:*", "aws-marketplace:*",
    "aws-portal:*", "budgets:*", "ce:*", "chime:*", "cloudfront:*", "config:*", "cur:*",
    "directconnect:*", "ec2:DescribeRegions", "ec2:DescribeTransitGateways",
    "ec2:DescribeVpnGateways", "fms:*", "globalaccelerator:*", "health:*", "iam:*",
    "identitystore:*", "importexport:*", "kms:*", "mobileanalytics:*", "networkmanager:*",
    "organizations:*", "pricing:*", "route53-recovery-cluster:*",
    "route53-recovery-control-config:*", "route53-recovery-readiness:*", "route53:*",
    "route53domains:*", "s3:GetAccountPublic*", "s3:ListAllMyBuckets",
    "s3:ListMultiRegionAccessPoints", "s3:PutAccountPublic*", "shield:*", "sso:*", "sts:*",
    "support:*", "trustedadvisor:*", "waf-regional:*", "waf:*", "wafv2:*", "wellarchitected:*",
  ]

  service_control_policies = {
    region-guardrail = {
      description = "Deny regional services outside us-east-1 and us-east-2."
      document = {
        Version = "2012-10-17"
        Statement = [{
          Sid       = "DenyOutsideApprovedRegions"
          Effect    = "Deny"
          NotAction = local.global_service_actions
          Resource  = "*"
          Condition = {
            StringNotEquals = { "aws:RequestedRegion" = local.allowed_regions }
            ArnNotLike      = { "aws:PrincipalARN" = [local.controltower_execution_role] }
          }
        }]
      }
    }

    baseline-protection = {
      description = "Protect organization membership, security services, and keys."
      document = {
        Version = "2012-10-17"
        Statement = [
          {
            Sid      = "DenyLeavingOrganization"
            Effect   = "Deny"
            Action   = "organizations:LeaveOrganization"
            Resource = "*"
          },
          {
            Sid       = "DenyRootUser"
            Effect    = "Deny"
            Action    = "*"
            Resource  = "*"
            Condition = { StringLike = { "aws:PrincipalArn" = "arn:aws:iam::*:root" } }
          },
          {
            Sid       = "DenyLongLivedIamUserCredentials"
            Effect    = "Deny"
            Action    = ["iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile"]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
          {
            Sid    = "DenySecurityBaselineChanges"
            Effect = "Deny"
            Action = [
              "access-analyzer:DeleteAnalyzer",
              "ec2:DisableEbsEncryptionByDefault",
              "guardduty:DeleteDetector",
              "guardduty:DisassociateFromAdministratorAccount",
              "guardduty:DisassociateFromMasterAccount",
              "s3:DeleteAccountPublicAccessBlock",
              "s3:PutAccountPublicAccessBlock",
              "securityhub:DisableSecurityHub",
              "securityhub:DisassociateFromAdministratorAccount",
              "securityhub:DisassociateFromMasterAccount",
            ]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
          {
            Sid       = "DenyKmsKeyDeletion"
            Effect    = "Deny"
            Action    = ["kms:ScheduleKeyDeletion", "kms:DisableKey"]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
        ]
      }
    }
  }

  scp_attachments = {
    for pair in setproduct(keys(local.service_control_policies), keys(local.workload_ous)) :
    "${pair[0]}/${pair[1]}" => { policy = pair[0], ou = pair[1] }
  }
}

resource "aws_organizations_policy" "scp" {
  for_each = local.service_control_policies

  name        = "symphony-${each.key}"
  description = each.value.description
  type        = "SERVICE_CONTROL_POLICY"
  content     = jsonencode(each.value.document)

  depends_on = [terraform_data.account_guard]
}

resource "aws_organizations_policy_attachment" "scp" {
  for_each = local.scp_attachments

  policy_id = aws_organizations_policy.scp[each.value.policy].id
  target_id = aws_organizations_organizational_unit.workload[each.value.ou].id
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! 8 passed, 0 failed.`

- [ ] **Step 5: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 6: Commit**

```bash
git add cloud/terraform/organization
git commit -m "feat(terraform): add region and baseline-protection SCPs"
```

---

### Task 6: ct-account module and account vending

**Files:**
- Create: `cloud/terraform/modules/ct-account/{versions.tf,variables.tf,main.tf,outputs.tf,.terraform.lock.hcl}`
- Test: `cloud/terraform/modules/ct-account/tests/ct_account.tftest.hcl`
- Create: `cloud/terraform/organization/accounts.tf`
- Modify (append): `cloud/terraform/organization/variables.tf`, `outputs.tf`, `tests/terraform.tfvars`, `tests/organization.tftest.hcl`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: `aws_organizations_organizational_unit.workload["infrastructure" | "sandbox" | "customers"]` (objects with `id` and `name`) and `aws_controltower_baseline.workload` (Task 4).
- Produces:
  - Module `modules/ct-account`. Inputs: `account_name`, `account_email`, `provisioned_product_name` (`[a-zA-Z0-9][a-zA-Z0-9._-]*`), `organizational_unit` (`{ id, name }`), `sso_user` (`{ email, first_name, last_name }`), and `product_name` / `provisioning_artifact_name` (both default to `AWS Control Tower Account Factory`). Output: `account_id`.
  - In the organization root: `module.shared_services_account`, `module.fixture_account`, `module.tenant_account[<tenant_id>]`, and variable `tenants`, a map of `{ display_name, account_email, workload_region, monthly_budget_usd, billing_contacts }`.
  - Outputs `shared_services_account_id`, `fixture_account_id`, and `tenant_accounts` (`{ <tenant_id> = { account_id, workload_region } }`), consumed by Tasks 10–13.

Account Factory takes the parameters `AccountName`, `AccountEmail`, `ManagedOrganizationalUnit` (formatted `"<OU name> (<ou-id>)"`), `SSOUserEmail`, `SSOUserFirstName`, and `SSOUserLastName`. The new account ID comes back in the provisioned product's `outputs`, under key `AccountId`.

- [ ] **Step 1: Write the failing module test**

Create `cloud/terraform/modules/ct-account/tests/ct_account.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  account_name             = "Customer Acme"
  account_email            = "aws+acme@example.com"
  provisioned_product_name = "symphony-tenant-acme"
  organizational_unit      = { id = "ou-abcd-customers", name = "Customers" }
  sso_user                 = { email = "ops@example.com", first_name = "Symphony", last_name = "Operations" }
}

run "passes_every_account_factory_parameter" {
  command = plan

  assert {
    condition = toset([for p in aws_servicecatalog_provisioned_product.this.provisioning_parameters : p.key]) == toset([
      "AccountName", "AccountEmail", "ManagedOrganizationalUnit", "SSOUserEmail", "SSOUserFirstName", "SSOUserLastName",
    ])
    error_message = "Account Factory needs exactly its six provisioning parameters."
  }
}

run "formats_the_managed_ou_as_name_and_id" {
  command = plan

  assert {
    condition     = one([for p in aws_servicecatalog_provisioned_product.this.provisioning_parameters : p.value if p.key == "ManagedOrganizationalUnit"]) == "Customers (ou-abcd-customers)"
    error_message = "ManagedOrganizationalUnit must be 'Name (ou-id)'."
  }
}

run "exposes_the_vended_account_id" {
  command = plan

  override_resource {
    target          = aws_servicecatalog_provisioned_product.this
    override_during = plan
    values = {
      outputs = [
        { key = "AccountEmail", value = "aws+acme@example.com", description = "" },
        { key = "AccountId", value = "444444444444", description = "" },
      ]
    }
  }

  assert {
    condition     = output.account_id == "444444444444"
    error_message = "account_id must come from the AccountId provisioned product output."
  }
}

run "rejects_spaces_in_the_product_name" {
  command = plan

  variables {
    provisioned_product_name = "symphony tenant acme"
  }

  expect_failures = [var.provisioned_product_name]
}
```

- [ ] **Step 2: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("modules/ct-account/main.tf", PREVENT_DESTROY, 1, "Vended accounts must not be terminated by Terraform."),
```


- [ ] **Step 3: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/modules/ct-account init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/ct-account test
```

Expected: FAIL with undeclared references.

- [ ] **Step 4: Implement the module**

Copy `cloud/terraform/modules/secure-bucket/versions.tf` to `cloud/terraform/modules/ct-account/versions.tf` unchanged. Then:

Create `cloud/terraform/modules/ct-account/variables.tf`:

```hcl
variable "account_name" {
  description = "Display name of the vended account."
  type        = string
}

variable "account_email" {
  description = "Unique root email address for the vended account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.account_email))
    error_message = "account_email must be an email address."
  }
}

variable "provisioned_product_name" {
  description = "Service Catalog provisioned product name; letters, digits, dot, underscore, hyphen."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$", var.provisioned_product_name))
    error_message = "provisioned_product_name may contain only letters, digits, '.', '_', and '-'."
  }
}

variable "organizational_unit" {
  description = "Registered OU that receives the account."
  type = object({
    id   = string
    name = string
  })
}

variable "sso_user" {
  description = "Identity Center user that Account Factory grants access to the new account."
  type = object({
    email      = string
    first_name = string
    last_name  = string
  })
}

variable "product_name" {
  description = "Account Factory product name in Service Catalog."
  type        = string
  default     = "AWS Control Tower Account Factory"
}

variable "provisioning_artifact_name" {
  description = "Account Factory provisioning artifact name."
  type        = string
  default     = "AWS Control Tower Account Factory"
}
```

Create `cloud/terraform/modules/ct-account/main.tf`:

```hcl
# Vends one account through Control Tower Account Factory. Removing this
# resource would terminate the provisioned product, so destruction is blocked.
resource "aws_servicecatalog_provisioned_product" "this" {
  name                       = var.provisioned_product_name
  product_name               = var.product_name
  provisioning_artifact_name = var.provisioning_artifact_name

  provisioning_parameters {
    key   = "AccountName"
    value = var.account_name
  }

  provisioning_parameters {
    key   = "AccountEmail"
    value = var.account_email
  }

  provisioning_parameters {
    key   = "ManagedOrganizationalUnit"
    value = "${var.organizational_unit.name} (${var.organizational_unit.id})"
  }

  provisioning_parameters {
    key   = "SSOUserEmail"
    value = var.sso_user.email
  }

  provisioning_parameters {
    key   = "SSOUserFirstName"
    value = var.sso_user.first_name
  }

  provisioning_parameters {
    key   = "SSOUserLastName"
    value = var.sso_user.last_name
  }

  timeouts {
    create = "60m"
    update = "60m"
    delete = "60m"
  }

  lifecycle {
    prevent_destroy = true
  }
}
```

Create `cloud/terraform/modules/ct-account/outputs.tf`:

```hcl
output "account_id" {
  description = "ID of the vended account."
  value       = one([for output in aws_servicecatalog_provisioned_product.this.outputs : output.value if output.key == "AccountId"])
}
```

- [ ] **Step 5: Run the module tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/modules/ct-account init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/ct-account providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/modules/ct-account validate
terraform -chdir=cloud/terraform/modules/ct-account test
```

Expected: `Success! The configuration is valid.` and `Success! 4 passed, 0 failed.`

- [ ] **Step 6: Write the failing organization tests**

Append to `cloud/terraform/organization/tests/terraform.tfvars`, separated from the existing content by one blank line:

```hcl
account_factory_portfolio_id = "port-abc123example"
account_factory_principal    = { arn = "arn:aws:iam::111111111111:role/SymphonyTerraform" }
account_factory_sso_user     = { email = "ops@example.com", first_name = "Symphony", last_name = "Operations" }
shared_services_account      = { email = "aws+shared@example.com" }
fixture_account              = { email = "aws+fixture@example.com" }
tenants = {
  acme = {
    display_name       = "Acme Pilot"
    account_email      = "aws+acme@example.com"
    workload_region    = "us-east-1"
    monthly_budget_usd = 250
    billing_contacts   = ["billing@acme.example.com"]
  }
}
```

Append to `cloud/terraform/organization/tests/organization.tftest.hcl`, separated from the existing content by one blank line:

```hcl
# --- accounts ---
run "vends_one_account_per_tenant" {
  command = plan

  assert {
    condition     = toset(keys(module.tenant_account)) == toset(["acme"])
    error_message = "Each configured tenant needs exactly one vended account."
  }

  assert {
    condition     = aws_servicecatalog_principal_portfolio_association.terraform.principal_arn == "arn:aws:iam::111111111111:role/SymphonyTerraform"
    error_message = "The Terraform principal must be associated with the Account Factory portfolio."
  }
}

run "publishes_tenant_account_ids_and_regions" {
  command = plan

  assert {
    condition     = output.tenant_accounts["acme"].account_id == "444444444444"
    error_message = "tenant_accounts must expose the vended account ID."
  }

  assert {
    condition     = output.tenant_accounts["acme"].workload_region == "us-east-1"
    error_message = "tenant_accounts must expose the tenant workload Region."
  }
}

run "rejects_a_reused_account_email" {
  command = plan

  variables {
    fixture_account = { email = "AWS+Shared@example.com" }
  }

  expect_failures = [var.tenants]
}

run "rejects_a_region_outside_the_pilot_pair" {
  command = plan

  variables {
    tenants = {
      acme = {
        display_name       = "Acme Pilot"
        account_email      = "aws+acme@example.com"
        workload_region    = "us-west-2"
        monthly_budget_usd = 250
        billing_contacts   = ["billing@acme.example.com"]
      }
    }
  }

  expect_failures = [var.tenants]
}

run "rejects_an_invalid_tenant_id" {
  command = plan

  variables {
    tenants = {
      "Acme_Corp" = {
        display_name       = "Acme Pilot"
        account_email      = "aws+acme@example.com"
        workload_region    = "us-east-1"
        monthly_budget_usd = 250
        billing_contacts   = ["billing@acme.example.com"]
      }
    }
  }

  expect_failures = [var.tenants]
}
```

Run `terraform -chdir=cloud/terraform/organization init -backend=false -input=false && terraform -chdir=cloud/terraform/organization test`. Expected: FAIL, because `tests/terraform.tfvars` sets undeclared variables and `module.tenant_account` does not exist.

- [ ] **Step 7: Implement account vending**

Append to `cloud/terraform/organization/variables.tf`, separated from the existing content by one blank line:

```hcl
variable "account_factory_portfolio_id" {
  description = "ID of the 'AWS Control Tower Account Factory Portfolio' (aws servicecatalog list-portfolios)."
  type        = string

  validation {
    condition     = can(regex("^port-[a-z0-9]+$", var.account_factory_portfolio_id))
    error_message = "account_factory_portfolio_id must look like port-abc123."
  }
}

variable "account_factory_principal" {
  description = "IAM principal running Terraform, granted access to the Account Factory portfolio."
  type = object({
    arn  = string
    type = optional(string, "IAM")
  })

  validation {
    condition     = contains(["IAM", "IAM_PATTERN"], var.account_factory_principal.type)
    error_message = "account_factory_principal.type must be IAM or IAM_PATTERN."
  }
}

variable "account_factory_sso_user" {
  description = "Identity Center user Account Factory assigns to each vended account (Symphony operations)."
  type = object({
    email      = string
    first_name = string
    last_name  = string
  })
}

variable "shared_services_account" {
  description = "Shared services account in the Infrastructure OU."
  type = object({
    name  = optional(string, "Shared Services")
    email = string
  })
}

variable "fixture_account" {
  description = "Low-cost integration fixture account in the Sandbox OU."
  type = object({
    name  = optional(string, "Integration Fixture")
    email = string
  })
}

variable "tenants" {
  description = "Customer workload accounts keyed by tenant ID."
  type = map(object({
    display_name       = string
    account_email      = string
    workload_region    = string
    monthly_budget_usd = number
    billing_contacts   = list(string)
  }))

  validation {
    condition     = length(var.tenants) >= 1 && alltrue([for id in keys(var.tenants) : can(regex("^[a-z][a-z0-9-]{2,30}$", id))])
    error_message = "Define at least one tenant; tenant IDs are 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }

  validation {
    condition     = alltrue([for t in var.tenants : contains(["us-east-1", "us-east-2"], t.workload_region)])
    error_message = "Tenant workload_region must be us-east-1 or us-east-2."
  }

  validation {
    condition = length(distinct([
      for email in concat(
        [var.shared_services_account.email, var.fixture_account.email],
        [for t in var.tenants : t.account_email],
      ) : lower(email)
    ])) == 2 + length(var.tenants)
    error_message = "Every vended account needs its own email address."
  }
}
```

Create `cloud/terraform/organization/accounts.tf`:

```hcl
# Lets the Terraform principal launch the Account Factory product.
resource "aws_servicecatalog_principal_portfolio_association" "terraform" {
  portfolio_id   = var.account_factory_portfolio_id
  principal_arn  = var.account_factory_principal.arn
  principal_type = var.account_factory_principal.type

  depends_on = [terraform_data.account_guard]
}

module "shared_services_account" {
  source = "../modules/ct-account"

  account_name             = var.shared_services_account.name
  account_email            = var.shared_services_account.email
  provisioned_product_name = "symphony-shared-services"
  organizational_unit      = aws_organizations_organizational_unit.workload["infrastructure"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}

module "fixture_account" {
  source = "../modules/ct-account"

  account_name             = var.fixture_account.name
  account_email            = var.fixture_account.email
  provisioned_product_name = "symphony-integration-fixture"
  organizational_unit      = aws_organizations_organizational_unit.workload["sandbox"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}

module "tenant_account" {
  source   = "../modules/ct-account"
  for_each = var.tenants

  account_name             = "Customer ${each.value.display_name}"
  account_email            = each.value.account_email
  provisioned_product_name = "symphony-tenant-${each.key}"
  organizational_unit      = aws_organizations_organizational_unit.workload["customers"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}
```

Append to `cloud/terraform/organization/outputs.tf`, separated from the existing content by one blank line:

```hcl
output "shared_services_account_id" {
  description = "Shared services account ID."
  value       = module.shared_services_account.account_id
}

output "fixture_account_id" {
  description = "Integration fixture account ID."
  value       = module.fixture_account.account_id
}

output "tenant_accounts" {
  description = "Tenant account ID and workload Region keyed by tenant ID."
  value = {
    for id, account in module.tenant_account : id => {
      account_id      = account.account_id
      workload_region = var.tenants[id].workload_region
    }
  }
}
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! 13 passed, 0 failed.`

- [ ] **Step 9: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 10: Commit**

```bash
git add cloud/terraform/modules/ct-account cloud/terraform/organization cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): vend shared, fixture, and tenant accounts via Account Factory"
```

---

### Task 7: finding-alerts module and delegated security services

**Files:**
- Create: `cloud/terraform/modules/finding-alerts/{versions.tf,variables.tf,main.tf,outputs.tf,.terraform.lock.hcl}`
- Test: `cloud/terraform/modules/finding-alerts/tests/finding_alerts.tftest.hcl`
- Create: `cloud/terraform/organization/security_services.tf`
- Modify (append): `cloud/terraform/organization/variables.tf`, `outputs.tf`, `tests/terraform.tfvars`, `tests/organization.tftest.hcl`

**Interfaces:**
- Consumes: the `aws.audit` provider, `var.audit_account_id`, `local.allowed_regions`, and `terraform_data.account_guard` (Task 4).
- Produces: module `modules/finding-alerts`, with inputs `account_id`, `region`, `subscriber_emails` (non-empty), and `guardduty_min_severity` (default 7), and output `topic_arn`. In the root: variable `security_contacts` and output `security_alert_topic_arns`, keyed by Region.

Design notes:
- GuardDuty and Security Hub CSPM are Regional. In provider 6.x, resources take a `region` argument, so one provider per account covers both Regions with `for_each`.
- Detectors and hubs are created in the audit account **before** delegation, so the delegation call doesn't create a conflicting one. The management account also gets a detector and hub per Region, because org auto-enrollment does not cover the management account.
- Security Hub standards stay off (`auto_enable_standards = "NONE"`, `enable_default_standards = false`). Control Tower deploys its own service-managed standard, and extra standards add per-check cost.
- EventBridge cannot publish to an SNS topic encrypted with the AWS managed key, so each topic has its own customer managed key whose policy allows `events.amazonaws.com`.

- [ ] **Step 1: Write the failing module test**

Create `cloud/terraform/modules/finding-alerts/tests/finding_alerts.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  account_id        = "333333333333"
  region            = "us-east-2"
  subscriber_emails = ["security@example.com"]
}

run "forwards_high_guardduty_and_securityhub_findings" {
  command = plan

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.findings["guardduty"].event_pattern).detail.severity[0].numeric == [">=", 7]
    error_message = "GuardDuty findings of severity 7 and above must be forwarded."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.findings["securityhub"].event_pattern).detail.findings.Severity.Label == ["HIGH", "CRITICAL"]
    error_message = "Security Hub HIGH and CRITICAL findings must be forwarded."
  }

  assert {
    condition     = alltrue([for r in aws_cloudwatch_event_rule.findings : r.region == "us-east-2"])
    error_message = "Rules must be created in the module Region."
  }
}

run "only_finding_rules_may_publish" {
  command = plan

  assert {
    condition = toset(jsondecode(aws_sns_topic_policy.alerts.policy).Statement[0].Condition.ArnEquals["aws:SourceArn"]) == toset([
      "arn:aws:events:us-east-2:333333333333:rule/symphony-guardduty-findings",
      "arn:aws:events:us-east-2:333333333333:rule/symphony-securityhub-findings",
    ])
    error_message = "Only the two finding rules may publish to the topic."
  }
}

run "encrypts_the_topic_with_a_customer_key" {
  command = plan

  assert {
    condition     = aws_kms_key.alerts.enable_key_rotation
    error_message = "The alert key must rotate."
  }

  assert {
    condition     = jsondecode(aws_kms_key.alerts.policy).Statement[1].Principal.Service == "events.amazonaws.com"
    error_message = "EventBridge must be able to use the topic key."
  }
}

run "subscribes_every_contact" {
  command = plan

  assert {
    condition     = toset(keys(aws_sns_topic_subscription.email)) == toset(["security@example.com"])
    error_message = "Each security contact needs an email subscription."
  }
}

run "rejects_an_empty_contact_list" {
  command = plan

  variables {
    subscriber_emails = []
  }

  expect_failures = [var.subscriber_emails]
}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/modules/finding-alerts init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/finding-alerts test
```

Expected: FAIL with undeclared references.

- [ ] **Step 3: Implement the module**

Copy `cloud/terraform/modules/secure-bucket/versions.tf` to `cloud/terraform/modules/finding-alerts/versions.tf` unchanged. Then:

Create `cloud/terraform/modules/finding-alerts/variables.tf`:

```hcl
variable "account_id" {
  description = "Account that owns the alert topic (the delegated security administrator)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "region" {
  description = "Region whose GuardDuty and Security Hub findings this module forwards."
  type        = string
}

variable "subscriber_emails" {
  description = "Security contacts notified of high-severity findings."
  type        = list(string)

  validation {
    condition     = length(var.subscriber_emails) >= 1
    error_message = "At least one security contact is required, or findings reach nobody."
  }
}

variable "guardduty_min_severity" {
  description = "Lowest GuardDuty severity forwarded (7 = high)."
  type        = number
  default     = 7
}
```

Create `cloud/terraform/modules/finding-alerts/main.tf`:

```hcl
locals {
  name = "symphony-security-findings"
  rule_names = {
    guardduty   = "symphony-guardduty-findings"
    securityhub = "symphony-securityhub-findings"
  }
  event_patterns = {
    guardduty = {
      source        = ["aws.guardduty"]
      "detail-type" = ["GuardDuty Finding"]
      detail        = { severity = [{ numeric = [">=", var.guardduty_min_severity] }] }
    }
    securityhub = {
      source        = ["aws.securityhub"]
      "detail-type" = ["Security Hub Findings - Imported"]
      detail = {
        findings = {
          Severity    = { Label = ["HIGH", "CRITICAL"] }
          Workflow    = { Status = ["NEW"] }
          RecordState = ["ACTIVE"]
        }
      }
    }
  }
  rule_arns = [for name in values(local.rule_names) : "arn:aws:events:${var.region}:${var.account_id}:rule/${name}"]
}

# EventBridge cannot publish to an SNS topic encrypted with the AWS managed key,
# so the topic gets its own customer managed key.
resource "aws_kms_key" "alerts" {
  region                  = var.region
  description             = "Encrypts Symphony security finding notifications"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableAccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowEventBridgePublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
      },
    ]
  })
}

resource "aws_sns_topic" "alerts" {
  region            = var.region
  name              = local.name
  kms_master_key_id = aws_kms_key.alerts.arn
}

resource "aws_sns_topic_policy" "alerts" {
  region = var.region
  arn    = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowFindingRulesToPublish"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = "arn:aws:sns:${var.region}:${var.account_id}:${local.name}"
      Condition = { ArnEquals = { "aws:SourceArn" = local.rule_arns } }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "findings" {
  for_each = local.rule_names

  region        = var.region
  name          = each.value
  description   = "Forward high-severity ${each.key} findings to security contacts"
  event_pattern = jsonencode(local.event_patterns[each.key])
}

resource "aws_cloudwatch_event_target" "findings" {
  for_each = local.rule_names

  region    = var.region
  rule      = aws_cloudwatch_event_rule.findings[each.key].name
  target_id = "sns"
  arn       = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.subscriber_emails)

  region    = var.region
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}
```

Create `cloud/terraform/modules/finding-alerts/outputs.tf`:

```hcl
output "topic_arn" {
  description = "SNS topic receiving high-severity findings."
  value       = aws_sns_topic.alerts.arn
}
```

- [ ] **Step 4: Run the module tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/modules/finding-alerts init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/finding-alerts providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/modules/finding-alerts validate
terraform -chdir=cloud/terraform/modules/finding-alerts test
```

Expected: `Success! The configuration is valid.` and `Success! 5 passed, 0 failed.`

- [ ] **Step 5: Write the failing organization tests**

Append to `cloud/terraform/organization/tests/terraform.tfvars`, separated from the existing content by one blank line:

```hcl
security_contacts = ["security@example.com"]
```

Append to `cloud/terraform/organization/tests/organization.tftest.hcl`, separated from the existing content by one blank line:

```hcl
# --- security services ---
run "delegates_guardduty_to_audit_in_both_regions" {
  command = plan

  assert {
    condition     = toset([for a in aws_guardduty_organization_admin_account.this : "${a.region}/${a.admin_account_id}"]) == toset(["us-east-1/333333333333", "us-east-2/333333333333"])
    error_message = "GuardDuty must be delegated to the audit account in both governed Regions."
  }

  assert {
    condition     = alltrue([for c in aws_guardduty_organization_configuration.this : c.auto_enable_organization_members == "ALL"])
    error_message = "GuardDuty must auto-enable every member account."
  }
}

run "delegates_security_hub_without_extra_standards" {
  command = plan

  assert {
    condition     = toset([for a in aws_securityhub_organization_admin_account.this : "${a.region}/${a.admin_account_id}"]) == toset(["us-east-1/333333333333", "us-east-2/333333333333"])
    error_message = "Security Hub must be delegated to the audit account in both governed Regions."
  }

  assert {
    condition     = alltrue([for c in aws_securityhub_organization_configuration.this : c.auto_enable && c.auto_enable_standards == "NONE"])
    error_message = "Security Hub must auto-enable accounts without enabling paid standards."
  }
}

run "enables_trusted_access_for_both_services" {
  command = plan

  assert {
    condition     = toset([for s in aws_organizations_aws_service_access.security : s.service_principal]) == toset(["guardduty.amazonaws.com", "securityhub.amazonaws.com"])
    error_message = "GuardDuty and Security Hub need trusted access."
  }
}

run "creates_finding_alerts_in_each_region" {
  command = plan

  assert {
    condition     = toset(keys(module.finding_alerts)) == toset(["us-east-1", "us-east-2"])
    error_message = "Each governed Region needs a finding alert topic."
  }
}
```

Run `terraform -chdir=cloud/terraform/organization test`. Expected: FAIL with undeclared references.

- [ ] **Step 6: Implement the security services**

Append to `cloud/terraform/organization/variables.tf`, separated from the existing content by one blank line:

```hcl
variable "security_contacts" {
  description = "Email addresses notified of high-severity GuardDuty and Security Hub findings."
  type        = list(string)

  validation {
    condition     = length(var.security_contacts) >= 1
    error_message = "At least one security contact is required."
  }
}
```

Create `cloud/terraform/organization/security_services.tf`:

```hcl
# GuardDuty and Security Hub CSPM are Regional: each governed Region gets a
# detector/hub in the audit account (the delegated administrator) and in the
# management account, then auto-enrollment for every member account.
locals {
  security_regions = toset(local.allowed_regions)
}

resource "aws_organizations_aws_service_access" "security" {
  for_each = toset(["guardduty.amazonaws.com", "securityhub.amazonaws.com"])

  service_principal = each.value

  depends_on = [terraform_data.account_guard]
}

resource "aws_guardduty_detector" "management" {
  for_each = local.security_regions

  region                       = each.value
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

resource "aws_guardduty_detector" "audit" {
  provider = aws.audit
  for_each = local.security_regions

  region                       = each.value
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

resource "aws_guardduty_organization_admin_account" "this" {
  for_each = local.security_regions

  region           = each.value
  admin_account_id = var.audit_account_id

  depends_on = [aws_organizations_aws_service_access.security, aws_guardduty_detector.audit]
}

resource "aws_guardduty_organization_configuration" "this" {
  provider = aws.audit
  for_each = local.security_regions

  region                           = each.value
  detector_id                      = aws_guardduty_detector.audit[each.key].id
  auto_enable_organization_members = "ALL"

  depends_on = [aws_guardduty_organization_admin_account.this]
}

resource "aws_securityhub_account" "management" {
  for_each = local.security_regions

  region                   = each.value
  enable_default_standards = false
}

resource "aws_securityhub_account" "audit" {
  provider = aws.audit
  for_each = local.security_regions

  region                   = each.value
  enable_default_standards = false
}

resource "aws_securityhub_organization_admin_account" "this" {
  for_each = local.security_regions

  region           = each.value
  admin_account_id = var.audit_account_id

  depends_on = [aws_organizations_aws_service_access.security, aws_securityhub_account.management, aws_securityhub_account.audit]
}

# Standards stay off: Control Tower deploys its own service-managed standard,
# and extra standards add per-check cost.
resource "aws_securityhub_organization_configuration" "this" {
  provider = aws.audit
  for_each = local.security_regions

  region                = each.value
  auto_enable           = true
  auto_enable_standards = "NONE"

  organization_configuration {
    configuration_type = "LOCAL"
  }

  depends_on = [aws_securityhub_organization_admin_account.this]
}

module "finding_alerts" {
  source   = "../modules/finding-alerts"
  for_each = local.security_regions

  providers = {
    aws = aws.audit
  }

  account_id        = var.audit_account_id
  region            = each.value
  subscriber_emails = var.security_contacts
}
```

Append to `cloud/terraform/organization/outputs.tf`, separated from the existing content by one blank line:

```hcl
output "security_alert_topic_arns" {
  description = "SNS topics in the audit account for high-severity findings, keyed by Region."
  value       = { for region, alerts in module.finding_alerts : region => alerts.topic_arn }
}
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! 17 passed, 0 failed.`

- [ ] **Step 8: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 9: Commit**

```bash
git add cloud/terraform/modules/finding-alerts cloud/terraform/organization
git commit -m "feat(terraform): delegate GuardDuty and Security Hub to audit with finding alerts"
```

---

### Task 8: organization root, part 5 (IAM Identity Center access)

**Files:**
- Create: `cloud/terraform/organization/identity_center.tf`
- Modify (append): `cloud/terraform/organization/variables.tf`, `outputs.tf`, `tests/terraform.tfvars`, `tests/organization.tftest.hcl`

**Interfaces:**
- Consumes: `module.shared_services_account.account_id`, `module.fixture_account.account_id`, `module.tenant_account[*].account_id`, and `var.tenants` (Task 6); `local.bootstrap.log_archive_account_id` and `var.audit_account_id`.
- Produces:
  - Variables `identity_center_instance_arn`, `identity_store_id`, and `identity_users`.
  - Permission sets `SymphonyPlatformAdmin` (AdministratorAccess, PT1H), `SymphonySecurityAudit` (SecurityAudit + ViewOnlyAccess), `CustomerAdmin` (PowerUserAccess), and `CustomerReadOnly` (ReadOnlyAccess).
  - Groups `symphony-operators`, `symphony-security`, `tenant-<id>-admins`, and `tenant-<id>-readonly`.
  - Output `permission_set_arns`.

Assignment rules:
- Operators get admin on the shared services, fixture, and tenant accounts only, never on management, audit, or log archive.
- Security reviewers get read-only security access to every account except management.
- Each tenant's groups reach only that tenant's account.

The instance ARN and identity store ID are explicit inputs, which the runbook gets from `aws sso-admin list-instances`. A data source would mock to empty values in every test file. MFA enforcement has no API; the runbook covers it.

- [ ] **Step 1: Write the failing tests**

Append to `cloud/terraform/organization/tests/terraform.tfvars`, separated from the existing content by one blank line:

```hcl
identity_center_instance_arn = "arn:aws:sso:::instance/ssoins-1111111111111111"
identity_store_id            = "d-1111111111"
```

Append to `cloud/terraform/organization/tests/organization.tftest.hcl`, separated from the existing content by one blank line:

```hcl
# --- identity center ---
run "creates_the_four_permission_sets" {
  command = plan

  assert {
    condition     = toset(keys(aws_ssoadmin_permission_set.this)) == toset(["SymphonyPlatformAdmin", "SymphonySecurityAudit", "CustomerAdmin", "CustomerReadOnly"])
    error_message = "Unexpected permission sets."
  }

  assert {
    condition     = aws_ssoadmin_permission_set.this["SymphonyPlatformAdmin"].session_duration == "PT1H"
    error_message = "Administrator sessions must be short."
  }
}

run "gives_customers_only_their_own_account" {
  command = plan

  assert {
    condition     = aws_ssoadmin_account_assignment.this["tenant-acme-admins/CustomerAdmin/tenant-acme"].target_id == "444444444444"
    error_message = "Tenant admins must be assigned to their tenant account."
  }

  assert {
    condition     = length([for key in keys(aws_ssoadmin_account_assignment.this) : key if strcontains(key, "/CustomerAdmin/")]) == 1
    error_message = "CustomerAdmin must be assigned exactly once per tenant."
  }
}

run "keeps_operators_out_of_management_and_security_accounts" {
  command = plan

  assert {
    condition     = !contains(keys(aws_ssoadmin_account_assignment.this), "symphony-operators/SymphonyPlatformAdmin/audit") && !contains(keys(aws_ssoadmin_account_assignment.this), "symphony-operators/SymphonyPlatformAdmin/log-archive")
    error_message = "Operators must not get administrator access to audit or log archive."
  }

  assert {
    condition     = contains(keys(aws_ssoadmin_account_assignment.this), "symphony-security/SymphonySecurityAudit/audit")
    error_message = "Security reviewers need read access to the audit account."
  }
}

run "adds_users_to_their_groups" {
  command = plan

  variables {
    identity_users = {
      jdoe = { given_name = "Jordan", family_name = "Doe", email = "jordan@acme.example.com", groups = ["tenant-acme-readonly"] }
    }
  }

  assert {
    condition     = toset(keys(aws_identitystore_group_membership.this)) == toset(["jdoe/tenant-acme-readonly"])
    error_message = "Each user must join exactly its configured groups."
  }
}

run "rejects_a_group_for_an_unknown_tenant" {
  command = plan

  variables {
    identity_users = {
      jdoe = { given_name = "Jordan", family_name = "Doe", email = "jordan@acme.example.com", groups = ["tenant-globex-admins"] }
    }
  }

  expect_failures = [var.identity_users]
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/organization test
```

Expected: FAIL with undeclared variable and resource errors.

- [ ] **Step 3: Implement Identity Center**

Append to `cloud/terraform/organization/variables.tf`, separated from the existing content by one blank line:

```hcl
variable "identity_center_instance_arn" {
  description = "Identity Center instance ARN (aws sso-admin list-instances)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:sso:::instance/ssoins-[a-f0-9]{16}$", var.identity_center_instance_arn))
    error_message = "identity_center_instance_arn must be an Identity Center instance ARN."
  }
}

variable "identity_store_id" {
  description = "Identity store ID of the Identity Center instance (aws sso-admin list-instances)."
  type        = string

  validation {
    condition     = can(regex("^d-[a-z0-9]{10}$", var.identity_store_id))
    error_message = "identity_store_id must look like d-1234567890."
  }
}

variable "identity_users" {
  description = "Identity Center users keyed by a stable handle. Groups: symphony-operators, symphony-security, tenant-<id>-admins, tenant-<id>-readonly."
  type = map(object({
    given_name  = string
    family_name = string
    email       = string
    groups      = list(string)
  }))
  default = {}

  validation {
    condition = alltrue(flatten([
      for user in var.identity_users : [
        for group in user.groups : contains(concat(
          ["symphony-operators", "symphony-security"],
          flatten([for id in keys(var.tenants) : ["tenant-${id}-admins", "tenant-${id}-readonly"]]),
        ), group)
      ]
    ]))
    error_message = "Every identity user group must be symphony-operators, symphony-security, or tenant-<id>-admins/readonly for a configured tenant."
  }
}
```

Create `cloud/terraform/organization/identity_center.tf`:

```hcl
# Symphony-owned permission sets, groups, and assignments. Control Tower's own
# AWSControlTower* groups and permission sets are left untouched.
# MFA enforcement is an Identity Center setting with no API; see the runbook.
locals {
  permission_sets = {
    SymphonyPlatformAdmin = {
      description      = "Symphony operators: full access to workload accounts."
      session_duration = "PT1H"
      managed_policies = ["arn:aws:iam::aws:policy/AdministratorAccess"]
    }
    SymphonySecurityAudit = {
      description      = "Symphony security: read security configuration, no data access."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/SecurityAudit", "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"]
    }
    CustomerAdmin = {
      description      = "Customer administrators: manage workload resources in their own account."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/PowerUserAccess"]
    }
    CustomerReadOnly = {
      description      = "Customer users: read-only access to their own account."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
    }
  }

  permission_set_policies = {
    for pair in flatten([
      for name, config in local.permission_sets : [
        for arn in config.managed_policies : { key = "${name}/${basename(arn)}", permission_set = name, arn = arn }
      ]
    ]) : pair.key => pair
  }

  identity_groups = merge(
    {
      symphony-operators = "Symphony platform operators"
      symphony-security  = "Symphony security reviewers"
    },
    merge([
      for id, tenant in var.tenants : {
        "tenant-${id}-admins"   = "${tenant.display_name} administrators"
        "tenant-${id}-readonly" = "${tenant.display_name} read-only users"
      }
    ]...),
  )

  workload_account_ids = merge(
    {
      shared-services = module.shared_services_account.account_id
      fixture         = module.fixture_account.account_id
    },
    { for id, account in module.tenant_account : "tenant-${id}" => account.account_id },
  )

  security_account_ids = merge(local.workload_account_ids, {
    audit       = var.audit_account_id
    log-archive = local.bootstrap.log_archive_account_id
  })

  account_assignments = merge(
    {
      for key, account_id in local.workload_account_ids : "symphony-operators/SymphonyPlatformAdmin/${key}" => {
        group = "symphony-operators", permission_set = "SymphonyPlatformAdmin", account_id = account_id
      }
    },
    {
      for key, account_id in local.security_account_ids : "symphony-security/SymphonySecurityAudit/${key}" => {
        group = "symphony-security", permission_set = "SymphonySecurityAudit", account_id = account_id
      }
    },
    merge([
      for id, account in module.tenant_account : {
        "tenant-${id}-admins/CustomerAdmin/tenant-${id}" = {
          group = "tenant-${id}-admins", permission_set = "CustomerAdmin", account_id = account.account_id
        }
        "tenant-${id}-readonly/CustomerReadOnly/tenant-${id}" = {
          group = "tenant-${id}-readonly", permission_set = "CustomerReadOnly", account_id = account.account_id
        }
      }
    ]...),
  )

  group_memberships = {
    for pair in flatten([
      for handle, user in var.identity_users : [
        for group in user.groups : { key = "${handle}/${group}", user = handle, group = group }
      ]
    ]) : pair.key => pair
  }
}

resource "aws_ssoadmin_permission_set" "this" {
  for_each = local.permission_sets

  name             = each.key
  description      = each.value.description
  instance_arn     = var.identity_center_instance_arn
  session_duration = each.value.session_duration

  depends_on = [terraform_data.account_guard]
}

resource "aws_ssoadmin_managed_policy_attachment" "this" {
  for_each = local.permission_set_policies

  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.permission_set].arn
  managed_policy_arn = each.value.arn
}

resource "aws_identitystore_group" "this" {
  for_each = local.identity_groups

  identity_store_id = var.identity_store_id
  display_name      = each.key
  description       = each.value
}

resource "aws_identitystore_user" "this" {
  for_each = var.identity_users

  identity_store_id = var.identity_store_id
  user_name         = each.value.email
  display_name      = "${each.value.given_name} ${each.value.family_name}"

  name {
    given_name  = each.value.given_name
    family_name = each.value.family_name
  }

  emails {
    value   = each.value.email
    primary = true
    type    = "work"
  }
}

resource "aws_identitystore_group_membership" "this" {
  for_each = local.group_memberships

  identity_store_id = var.identity_store_id
  group_id          = aws_identitystore_group.this[each.value.group].group_id
  member_id         = aws_identitystore_user.this[each.value.user].user_id
}

resource "aws_ssoadmin_account_assignment" "this" {
  for_each = local.account_assignments

  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.permission_set].arn
  principal_type     = "GROUP"
  principal_id       = aws_identitystore_group.this[each.value.group].group_id
  target_type        = "AWS_ACCOUNT"
  target_id          = each.value.account_id

  depends_on = [aws_ssoadmin_managed_policy_attachment.this]
}
```

Append to `cloud/terraform/organization/outputs.tf`, separated from the existing content by one blank line:

```hcl
output "permission_set_arns" {
  description = "Symphony Identity Center permission set ARNs keyed by name."
  value       = { for name, ps in aws_ssoadmin_permission_set.this : name => ps.arn }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! 22 passed, 0 failed.`

- [ ] **Step 5: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 6: Commit**

```bash
git add cloud/terraform/organization
git commit -m "feat(terraform): add Identity Center permission sets, groups, and assignments"
```

---

### Task 9: organization root, part 6 (tenant budgets) and README

**Files:**
- Create: `cloud/terraform/organization/budgets.tf`, `cloud/terraform/organization/README.md`
- Modify: `cloud/terraform/organization/variables.tf` (append, and edit `variable "tenants"`), `outputs.tf`, `tests/terraform.tfvars`, `tests/organization.tftest.hcl`

**Interfaces:**
- Consumes: `var.tenants`, `module.tenant_account[*].account_id`, and `module.fixture_account.account_id` (Task 6).
- Produces: variables `operations_contacts` (non-empty) and `budget_delivery_test_amount_usd` (nullable); `aws_budgets_budget.tenant[<tenant_id>]`, named `symphony-tenant-<id>-monthly`; the optional `aws_budgets_budget.delivery_test[0]`; and output `tenant_budget_names`.

- [ ] **Step 1: Write the failing tests**

Append to `cloud/terraform/organization/tests/terraform.tfvars`, separated from the existing content by one blank line:

```hcl
operations_contacts = ["ops@example.com"]
```

Append to `cloud/terraform/organization/tests/organization.tftest.hcl`, separated from the existing content by one blank line:

```hcl
# --- budgets ---
run "alerts_at_80_forecast_80_actual_and_100_actual" {
  command = plan

  assert {
    condition = toset([for n in aws_budgets_budget.tenant["acme"].notification : "${n.notification_type}/${n.threshold}"]) == toset([
      "FORECASTED/80", "ACTUAL/80", "ACTUAL/100",
    ])
    error_message = "Tenant budgets need the 80% forecast, 80% actual, and 100% actual alerts."
  }

  assert {
    condition     = aws_budgets_budget.tenant["acme"].limit_amount == "250.00"
    error_message = "The budget limit must equal the tenant's monthly amount."
  }
}

run "filters_the_budget_to_the_tenant_account" {
  command = plan

  assert {
    condition     = one(aws_budgets_budget.tenant["acme"].cost_filter).name == "LinkedAccount" && toset(one(aws_budgets_budget.tenant["acme"].cost_filter).values) == toset(["444444444444"])
    error_message = "The budget must cover only the tenant's linked account."
  }
}

run "notifies_billing_and_operations" {
  command = plan

  assert {
    condition     = alltrue([for n in aws_budgets_budget.tenant["acme"].notification : toset(n.subscriber_email_addresses) == toset(["billing@acme.example.com", "ops@example.com"])])
    error_message = "Every alert must reach the customer billing contact and Symphony operations."
  }
}

run "omits_the_delivery_test_budget_by_default" {
  command = plan

  assert {
    condition     = length(aws_budgets_budget.delivery_test) == 0
    error_message = "The delivery test budget must be opt-in."
  }
}

run "creates_the_delivery_test_budget_when_requested" {
  command = plan

  variables {
    budget_delivery_test_amount_usd = 1
  }

  assert {
    condition     = aws_budgets_budget.delivery_test[0].limit_amount == "1.00"
    error_message = "The delivery test budget must use the requested amount."
  }
}

run "rejects_a_tenant_without_billing_contacts" {
  command = plan

  variables {
    tenants = {
      acme = {
        display_name       = "Acme Pilot"
        account_email      = "aws+acme@example.com"
        workload_region    = "us-east-1"
        monthly_budget_usd = 250
        billing_contacts   = []
      }
    }
  }

  expect_failures = [var.tenants]
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
terraform -chdir=cloud/terraform/organization test
```

Expected: FAIL, because `operations_contacts` is undeclared and `aws_budgets_budget.tenant` doesn't exist.

- [ ] **Step 3: Implement budgets**

Append to `cloud/terraform/organization/variables.tf`, separated from the existing content by one blank line:

```hcl
variable "operations_contacts" {
  description = "Symphony operations email addresses copied on every tenant budget alert."
  type        = list(string)

  validation {
    condition     = length(var.operations_contacts) >= 1
    error_message = "At least one operations contact is required."
  }
}

variable "budget_delivery_test_amount_usd" {
  description = "Set to a tiny amount (for example 1) to create a fixture-account budget that proves alert delivery; null removes it."
  type        = number
  default     = null
}
```

In `variable "tenants"` in the same file, insert these two validation blocks immediately before the email-uniqueness validation, the block whose condition begins `length(distinct([`:

```hcl
  validation {
    condition     = alltrue([for t in var.tenants : t.monthly_budget_usd > 0])
    error_message = "Each tenant needs a positive monthly_budget_usd."
  }

  validation {
    condition     = alltrue([for t in var.tenants : length(t.billing_contacts) >= 1 && length(distinct(concat(t.billing_contacts, var.operations_contacts))) <= 10])
    error_message = "Each tenant needs at least one billing contact, and at most 10 budget recipients including operations contacts."
  }
```

Create `cloud/terraform/organization/budgets.tf`:

```hcl
# AWS Budgets notify; they are not a spending cap. Defaults from PLAN.md:
# 80% forecast, 80% actual, and 100% actual of the customer's monthly amount.
locals {
  budget_notifications = [
    { notification_type = "FORECASTED", threshold = 80 },
    { notification_type = "ACTUAL", threshold = 80 },
    { notification_type = "ACTUAL", threshold = 100 },
  ]
}

resource "aws_budgets_budget" "tenant" {
  for_each = var.tenants

  name         = "symphony-tenant-${each.key}-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", each.value.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "LinkedAccount"
    values = [module.tenant_account[each.key].account_id]
  }

  dynamic "notification" {
    for_each = local.budget_notifications

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = distinct(concat(each.value.billing_contacts, var.operations_contacts))
    }
  }

  depends_on = [terraform_data.account_guard]
}

# Temporary low-threshold budget on the fixture account to prove that
# notifications reach operations. Remove it by setting the amount to null.
resource "aws_budgets_budget" "delivery_test" {
  count = var.budget_delivery_test_amount_usd == null ? 0 : 1

  name         = "symphony-budget-delivery-test"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_delivery_test_amount_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "LinkedAccount"
    values = [module.fixture_account.account_id]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.operations_contacts
  }
}
```

Append to `cloud/terraform/organization/outputs.tf`, separated from the existing content by one blank line:

```hcl
output "tenant_budget_names" {
  description = "AWS Budgets names keyed by tenant ID."
  value       = { for id, budget in aws_budgets_budget.tenant : id => budget.name }
}
```

Create `cloud/terraform/organization/README.md`:

```markdown
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/organization init -backend=false -input=false
terraform -chdir=cloud/terraform/organization validate
terraform -chdir=cloud/terraform/organization test
```

Expected: `Success! 28 passed, 0 failed.`

Then confirm the lock file still matches: `terraform -chdir=cloud/terraform/organization init -backend=false -input=false -lockfile=readonly` must succeed.

- [ ] **Step 5: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 6: Commit**

```bash
git add cloud/terraform/organization
git commit -m "feat(terraform): add tenant budgets and organization README"
```

---

### Task 10: account-hygiene module and shared-services root

**Files:**
- Create: `cloud/terraform/modules/account-hygiene/{versions.tf,variables.tf,main.tf,.terraform.lock.hcl}`
- Test: `cloud/terraform/modules/account-hygiene/tests/account_hygiene.tftest.hcl`
- Create: `cloud/terraform/shared-services/{versions.tf,providers.tf,remote_state.tf,guard.tf,variables.tf,main.tf,outputs.tf,terraform.tfvars.example,README.md,.terraform.lock.hcl}`
- Test: `cloud/terraform/shared-services/tests/shared_services.tftest.hcl`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: organization output `shared_services_account_id` (key `symphony/organization.tfstate`); `modules/secure-bucket`.
- Produces:
  - Module `modules/account-hygiene`. Its input `regions` must be a non-empty subset of `us-east-1`/`us-east-2`. It creates the account S3 public access block, plus per-Region EBS default encryption and an `ACCOUNT` Access Analyzer. Tasks 11–12 reuse it.
  - Shared-services outputs `artifacts_bucket_name` and `artifacts_kms_key_arn`.
  - `shared-services/remote_state.tf`, which Tasks 11–13 copy unchanged.

- [ ] **Step 1: Write the failing module test**

Create `cloud/terraform/modules/account-hygiene/tests/account_hygiene.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  regions = ["us-east-2"]
}

run "blocks_public_s3_access_account_wide" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_account_public_access_block.this.block_public_acls,
      aws_s3_account_public_access_block.this.block_public_policy,
      aws_s3_account_public_access_block.this.ignore_public_acls,
      aws_s3_account_public_access_block.this.restrict_public_buckets,
    ])
    error_message = "Account-level S3 public access must be fully blocked."
  }
}

run "encrypts_ebs_and_analyzes_access_per_region" {
  command = plan

  assert {
    condition     = aws_ebs_encryption_by_default.this["us-east-2"].enabled
    error_message = "EBS encryption by default must be on in each workload Region."
  }

  assert {
    condition     = aws_accessanalyzer_analyzer.this["us-east-2"].type == "ACCOUNT"
    error_message = "Each workload Region needs an account access analyzer."
  }
}

run "rejects_an_unapproved_region" {
  command = plan

  variables {
    regions = ["eu-west-1"]
  }

  expect_failures = [var.regions]
}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/modules/account-hygiene init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/account-hygiene test
```

Expected: FAIL with undeclared references.

- [ ] **Step 3: Implement the module**

Copy `cloud/terraform/modules/secure-bucket/versions.tf` to `cloud/terraform/modules/account-hygiene/versions.tf` unchanged. Then:

Create `cloud/terraform/modules/account-hygiene/variables.tf`:

```hcl
variable "regions" {
  description = "Regions where this account runs workloads."
  type        = set(string)

  validation {
    condition     = length(var.regions) >= 1 && alltrue([for r in var.regions : contains(["us-east-1", "us-east-2"], r)])
    error_message = "regions must be a non-empty subset of us-east-1 and us-east-2."
  }
}
```

Create `cloud/terraform/modules/account-hygiene/main.tf`:

```hcl
# Account-wide defaults every Symphony member account gets. The
# baseline-protection SCP stops anyone but AWSControlTowerExecution reverting them.
resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ebs_encryption_by_default" "this" {
  for_each = var.regions

  region  = each.value
  enabled = true
}

resource "aws_accessanalyzer_analyzer" "this" {
  for_each = var.regions

  region        = each.value
  analyzer_name = "symphony-external-access"
  type          = "ACCOUNT"
}
```

- [ ] **Step 4: Run the module tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/modules/account-hygiene init -backend=false -input=false
terraform -chdir=cloud/terraform/modules/account-hygiene providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/modules/account-hygiene validate
terraform -chdir=cloud/terraform/modules/account-hygiene test
```

Expected: `Success! The configuration is valid.` and `Success! 3 passed, 0 failed.`

- [ ] **Step 5: Write the failing shared-services test**

Create `cloud/terraform/shared-services/tests/shared_services.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  shared_services_account_id = "555555555555"
  state_bucket               = "symphony-tfstate-111111111111"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "555555555555" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = { outputs = { shared_services_account_id = "555555555555" } }
}

run "creates_an_encrypted_artifact_bucket" {
  command = plan

  assert {
    condition     = module.artifacts_bucket.name == "symphony-artifacts-555555555555"
    error_message = "Artifact bucket name must include the shared services account ID."
  }

  assert {
    condition     = aws_kms_key.artifacts.enable_key_rotation
    error_message = "The artifact key must rotate."
  }
}

run "refuses_the_management_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "111111111111" }
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_an_account_the_organization_did_not_vend" {
  command = plan

  override_data {
    target = data.terraform_remote_state.organization
    values = { outputs = { shared_services_account_id = "666666666666" } }
  }

  expect_failures = [terraform_data.account_guard]
}
```

- [ ] **Step 6: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("shared-services/main.tf", PREVENT_DESTROY, 1, "The artifact key must not be destroyed."),
```

Add `"shared-services",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 7: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/shared-services init -backend=false -input=false
terraform -chdir=cloud/terraform/shared-services test
```

Expected: FAIL with undeclared references.

- [ ] **Step 8: Implement the root**

Copy `cloud/terraform/landing-zone/versions.tf` to `cloud/terraform/shared-services/versions.tf` unchanged. Then:

Create `cloud/terraform/shared-services/providers.tf`:

```hcl
locals {
  home_region = "us-east-1"
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.shared_services_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.shared_services_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-shared-services"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "shared-services"
    }
  }
}
```

Create `cloud/terraform/shared-services/remote_state.tf`:

```hcl
data "terraform_remote_state" "organization" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/organization.tfstate"
    region = "us-east-1"
  }
}

locals {
  organization = data.terraform_remote_state.organization.outputs
}
```

Create `cloud/terraform/shared-services/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.shared_services_account_id
      error_message = "The assumed role is not in the configured shared services account."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The shared services root must run in us-east-1."
    }

    precondition {
      condition     = try(local.organization.shared_services_account_id, null) == var.shared_services_account_id
      error_message = "shared_services_account_id does not match the account vended by the organization root."
    }
  }
}
```

Create `cloud/terraform/shared-services/variables.tf`:

```hcl
variable "shared_services_account_id" {
  description = "Shared services account ID (organization root output shared_services_account_id)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.shared_services_account_id))
    error_message = "shared_services_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}
```

Create `cloud/terraform/shared-services/main.tf`:

```hcl
module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [local.home_region]

  depends_on = [terraform_data.account_guard]
}

# Holds versioned build artifacts (Lambda packages, edge images). No tenant
# prompts, documents, or connector credentials belong here.
resource "aws_kms_key" "artifacts" {
  description             = "Encrypts Symphony shared build artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableSharedServicesIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.shared_services_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_alias" "artifacts" {
  name          = "alias/symphony/artifacts"
  target_key_id = aws_kms_key.artifacts.key_id
}

module "artifacts_bucket" {
  source = "../modules/secure-bucket"

  name        = "symphony-artifacts-${var.shared_services_account_id}"
  kms_key_arn = aws_kms_key.artifacts.arn
}
```

Create `cloud/terraform/shared-services/outputs.tf`:

```hcl
output "artifacts_bucket_name" {
  description = "Versioned bucket for Symphony build artifacts."
  value       = module.artifacts_bucket.name
}

output "artifacts_kms_key_arn" {
  description = "KMS key encrypting build artifacts."
  value       = aws_kms_key.artifacts.arn
}
```

Create `cloud/terraform/shared-services/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars (gitignored) and replace every value.
shared_services_account_id = "555555555555"
state_bucket               = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/shared-services/README.md`:

```markdown
# shared-services

Baseline for the shared services account: account hygiene (S3 public access block, EBS default encryption, IAM Access Analyzer) and a KMS-encrypted, versioned bucket for build artifacts such as Lambda packages and edge images.

| Item | Value |
| --- | --- |
| Account / Region | Shared services account via `AWSControlTowerExecution`, `us-east-1` |
| State key | `symphony/shared-services.tfstate` |
| Apply order | After `organization`. The guard checks `shared_services_account_id` against organization state. |
| Inputs | `shared_services_account_id`, `state_bucket` |
| Outputs | `artifacts_bucket_name`, `artifacts_kms_key_arn` |
| Cost-bearing resources | One KMS key, one S3 bucket (storage only). Access Analyzer external-access analyzers are free. |

No tenant prompts, documents, or connector credentials belong in this account. The artifact key and bucket have `prevent_destroy`.
```

- [ ] **Step 9: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/shared-services init -backend=false -input=false
terraform -chdir=cloud/terraform/shared-services providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/shared-services validate
terraform -chdir=cloud/terraform/shared-services test
```

Expected: `Success! The configuration is valid.` and `Success! 3 passed, 0 failed.`

- [ ] **Step 10: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 11: Commit**

```bash
git add cloud/terraform/modules/account-hygiene cloud/terraform/shared-services cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add account hygiene module and shared services baseline"
```

---

### Task 11: tenant-baseline root

**Files:**
- Create: `cloud/terraform/tenant-baseline/{versions.tf,providers.tf,remote_state.tf,guard.tf,variables.tf,main.tf,outputs.tf,terraform.tfvars.example,README.md,.terraform.lock.hcl}`
- Test: `cloud/terraform/tenant-baseline/tests/tenant_baseline.tftest.hcl`, `cloud/terraform/tenant-baseline/tests/terraform.tfvars`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: organization output `tenant_accounts[<tenant_id>]` (`account_id`, `workload_region`); `modules/account-hygiene`.
- Produces: outputs `tenant_data_kms_key_arn`, `device_registry_table_name`, `device_registry_table_arn`, `user_pool_id`, `user_pool_arn`, and `application_log_group_name`, all for Phase 3. Device registry items are keyed by `cert_serial`, with GSI `by_device_id` (hash `device_id`). `tenant-baseline/guard.tf` is reused unchanged by Task 13.

Notes from the AWS skills, applied in this task:
- The GSI uses a `key_schema` block, because provider 6.x deprecates `hash_key` inside `global_secondary_index`.
- The Cognito pool keeps the old email address active until a new one is verified.
- Log retention is bounded, at 30 days by default.

- [ ] **Step 1: Write the failing tests**

Create `cloud/terraform/tenant-baseline/tests/terraform.tfvars`:

```hcl
tenant_id         = "acme"
tenant_account_id = "444444444444"
workload_region   = "us-east-2"
state_bucket      = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/tenant-baseline/tests/tenant_baseline.tftest.hcl`:

```hcl
mock_provider "aws" {}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "444444444444" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-2" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = {
    outputs = {
      tenant_accounts = {
        acme = { account_id = "444444444444", workload_region = "us-east-2" }
      }
    }
  }
}

run "encrypts_tenant_data_with_a_rotating_key" {
  command = plan

  assert {
    condition     = aws_kms_key.tenant_data.enable_key_rotation
    error_message = "The tenant data key must rotate."
  }

  assert {
    condition     = jsondecode(aws_kms_key.tenant_data.policy).Statement[1].Principal.Service == "logs.us-east-2.amazonaws.com"
    error_message = "CloudWatch Logs in the workload Region must be able to use the key."
  }

  assert {
    condition     = aws_kms_alias.tenant_data.name == "alias/symphony/acme/data"
    error_message = "The key alias must include the tenant ID."
  }
}

run "protects_the_device_registry" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.device_registry.hash_key == "cert_serial"
    error_message = "Registry items are keyed by certificate serial."
  }

  assert {
    condition     = one(one([for i in aws_dynamodb_table.device_registry.global_secondary_index : i if i.name == "by_device_id"]).key_schema).attribute_name == "device_id"
    error_message = "The by_device_id index must be keyed by device_id for rotation lookups."
  }

  assert {
    condition     = aws_dynamodb_table.device_registry.deletion_protection_enabled
    error_message = "The device registry must have deletion protection."
  }

  assert {
    condition     = one(aws_dynamodb_table.device_registry.point_in_time_recovery).enabled
    error_message = "The device registry needs point-in-time recovery."
  }
}

run "requires_mfa_and_admin_created_users" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.users.mfa_configuration == "ON"
    error_message = "Pilot users must use MFA."
  }

  assert {
    condition     = one(aws_cognito_user_pool.users.admin_create_user_config).allow_admin_create_user_only
    error_message = "Self sign-up must be disabled."
  }

  assert {
    condition     = one(aws_cognito_user_pool.users.user_attribute_update_settings).attributes_require_verification_before_update == toset(["email"])
    error_message = "Email changes must be verified before they take effect."
  }

  assert {
    condition     = aws_cognito_user_pool.users.deletion_protection == "ACTIVE"
    error_message = "The user pool must have deletion protection."
  }
}

run "keeps_application_logs_short_lived_and_encrypted" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.application.name == "/symphony/acme/application" && aws_cloudwatch_log_group.application.retention_in_days == 30
    error_message = "Application logs must use the tenant log group and 30-day default retention."
  }
}

run "refuses_a_tenant_id_the_organization_did_not_vend" {
  command = plan

  variables {
    tenant_id = "globex"
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_a_region_that_differs_from_the_organization_record" {
  command = plan

  variables {
    workload_region = "us-east-1"
  }

  override_data {
    target = data.aws_region.current
    values = { region = "us-east-1" }
  }

  expect_failures = [terraform_data.account_guard]
}

run "rejects_a_region_outside_the_pilot_pair" {
  command = plan

  variables {
    workload_region = "us-west-2"
  }

  expect_failures = [var.workload_region]
}
```

- [ ] **Step 2: Pin the static invariants**

In `cloud/terraform/scripts/static_checks.py`, add these tuples at the end of the `REQUIRED` list (before its closing `]`):

```python
    ("tenant-baseline/main.tf", PREVENT_DESTROY, 3, "Tenant key, device registry, and user pool must not be destroyed."),
```

Add `"tenant-baseline",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run them to verify they fail**

```bash
terraform -chdir=cloud/terraform/tenant-baseline init -backend=false -input=false
terraform -chdir=cloud/terraform/tenant-baseline test
```

Expected: FAIL with undeclared references.

- [ ] **Step 4: Implement the root**

Copy `cloud/terraform/landing-zone/versions.tf` and `cloud/terraform/shared-services/remote_state.tf` into `cloud/terraform/tenant-baseline/` unchanged. Then:

Create `cloud/terraform/tenant-baseline/providers.tf`:

```hcl
provider "aws" {
  region              = var.workload_region
  allowed_account_ids = [var.tenant_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.tenant_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-tenant-baseline-${var.tenant_id}"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "tenant-baseline"
      TenantId      = var.tenant_id
    }
  }
}
```

Create `cloud/terraform/tenant-baseline/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  vended_tenant = try(local.organization.tenant_accounts[var.tenant_id], null)
}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.tenant_account_id
      error_message = "The assumed role is not in the configured tenant account."
    }

    precondition {
      condition     = data.aws_region.current.region == var.workload_region
      error_message = "The provider Region does not match workload_region."
    }

    precondition {
      condition     = try(local.vended_tenant.account_id, null) == var.tenant_account_id
      error_message = "tenant_id and tenant_account_id do not match an account vended by the organization root."
    }

    precondition {
      condition     = try(local.vended_tenant.workload_region, null) == var.workload_region
      error_message = "workload_region differs from the Region recorded for this tenant in the organization root."
    }
  }
}
```

Create `cloud/terraform/tenant-baseline/variables.tf`:

```hcl
variable "tenant_id" {
  description = "Tenant ID, matching a key of the organization root's tenants map."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.tenant_id))
    error_message = "tenant_id must be 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "tenant_account_id" {
  description = "Tenant workload account ID (organization root output tenant_accounts)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.tenant_account_id))
    error_message = "tenant_account_id must be 12 digits."
  }
}

variable "workload_region" {
  description = "Primary Region for this tenant's workload resources."
  type        = string

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.workload_region)
    error_message = "workload_region must be us-east-1 or us-east-2."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for tenant application log groups."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs retention value."
  }
}
```

Create `cloud/terraform/tenant-baseline/main.tf`:

```hcl
locals {
  name_prefix    = "symphony-${var.tenant_id}"
  log_group_name = "/symphony/${var.tenant_id}/application"
}

module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [var.workload_region]

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_key" "tenant_data" {
  description             = "Encrypts ${var.tenant_id} tenant data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableTenantAccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.tenant_account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowSymphonyLogGroups"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.workload_region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.workload_region}:${var.tenant_account_id}:log-group:/symphony/${var.tenant_id}/*"
          }
        }
      },
    ]
  })

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_alias" "tenant_data" {
  name          = "alias/symphony/${var.tenant_id}/data"
  target_key_id = aws_kms_key.tenant_data.key_id
}

resource "aws_cloudwatch_log_group" "application" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.tenant_data.arn
}

# One item per issued device certificate, so overlapping certificates can
# coexist during rotation. The Phase 3 authorizer looks up by serial.
resource "aws_dynamodb_table" "device_registry" {
  name                        = "${local.name_prefix}-device-registry"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "cert_serial"
  deletion_protection_enabled = true

  attribute {
    name = "cert_serial"
    type = "S"
  }

  attribute {
    name = "device_id"
    type = "S"
  }

  global_secondary_index {
    name            = "by_device_id"
    projection_type = "ALL"

    key_schema {
      attribute_name = "device_id"
      key_type       = "HASH"
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.tenant_data.arn
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Pilot end-user identities. Administrators create users; there is no self sign-up.
resource "aws_cognito_user_pool" "users" {
  name                     = "${local.name_prefix}-users"
  user_pool_tier           = "LITE"
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "ON"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  # Keep the old address active until a changed email is verified.
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}
```

Create `cloud/terraform/tenant-baseline/outputs.tf`:

```hcl
output "tenant_data_kms_key_arn" {
  description = "KMS key for tenant data at rest."
  value       = aws_kms_key.tenant_data.arn
}

output "device_registry_table_name" {
  description = "DynamoDB table of issued device certificates."
  value       = aws_dynamodb_table.device_registry.name
}

output "device_registry_table_arn" {
  description = "ARN of the device registry table."
  value       = aws_dynamodb_table.device_registry.arn
}

output "user_pool_id" {
  description = "Cognito user pool for pilot end users."
  value       = aws_cognito_user_pool.users.id
}

output "user_pool_arn" {
  description = "ARN of the pilot user pool."
  value       = aws_cognito_user_pool.users.arn
}

output "application_log_group_name" {
  description = "Encrypted log group for tenant application logs."
  value       = aws_cloudwatch_log_group.application.name
}
```

Create `cloud/terraform/tenant-baseline/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars (gitignored) and replace every value.
tenant_id         = "acme"
tenant_account_id = "444444444444"
workload_region   = "us-east-1"
state_bucket      = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/tenant-baseline/README.md`:

```markdown
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/tenant-baseline init -backend=false -input=false
terraform -chdir=cloud/terraform/tenant-baseline providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/tenant-baseline validate
terraform -chdir=cloud/terraform/tenant-baseline test
```

Expected: `Success! The configuration is valid.` and `Success! 7 passed, 0 failed.`

`validate` must print no deprecation warning.

- [ ] **Step 6: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 7: Commit**

```bash
git add cloud/terraform/tenant-baseline cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add tenant baseline with data key, device registry, and user pool"
```

---

### Task 12: sandbox-fixture root with synthetic documents

**Files:**
- Create: `cloud/terraform/sandbox-fixture/{versions.tf,providers.tf,remote_state.tf,guard.tf,variables.tf,main.tf,outputs.tf,terraform.tfvars.example,README.md,.terraform.lock.hcl}`
- Create: four synthetic files under `cloud/terraform/sandbox-fixture/fixtures/`
- Test: `cloud/terraform/sandbox-fixture/tests/sandbox_fixture.tftest.hcl`
- Modify: `cloud/terraform/scripts/static_checks.py`

**Interfaces:**
- Consumes: organization output `fixture_account_id`; modules `account-hygiene` and `secure-bucket`.
- Produces: outputs `documents_bucket_name`, `approved_prefix` (`approved-docs/`), `forbidden_prefix` (`forbidden-docs/`), and `documents_kms_key_arn`. The Phase 3 customer-side role template and the `cloud/tools/customer_documents` integration tests use them.

- [ ] **Step 1: Write the failing test**

Create `cloud/terraform/sandbox-fixture/tests/sandbox_fixture.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  fixture_account_id = "666666666666"
  state_bucket       = "symphony-tfstate-111111111111"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "666666666666" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = { outputs = { fixture_account_id = "666666666666" } }
}

run "uploads_approved_and_forbidden_synthetic_documents" {
  command = plan

  assert {
    condition = toset(keys(aws_s3_object.fixture)) == toset([
      "approved-docs/welcome.txt",
      "approved-docs/handbook/pto-policy.md",
      "approved-docs/inventory.csv",
      "forbidden-docs/acquisition-plan.txt",
    ])
    error_message = "Fixture objects must match the files under fixtures/."
  }

  assert {
    condition     = alltrue([for key in keys(aws_s3_object.fixture) : startswith(key, "approved-docs/") || startswith(key, "forbidden-docs/")])
    error_message = "Every fixture object must sit under the approved or forbidden prefix."
  }
}

run "labels_objects_and_names_the_bucket" {
  command = plan

  assert {
    condition     = alltrue([for o in aws_s3_object.fixture : o.content_type != null])
    error_message = "Every fixture object needs a text content type."
  }

  assert {
    condition     = module.documents_bucket.name == "symphony-fixture-666666666666-docs"
    error_message = "Fixture bucket name must include the fixture account ID."
  }
}

run "refuses_a_tenant_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "444444444444" }
  }

  expect_failures = [terraform_data.account_guard]
}
```

- [ ] **Step 2: Pin the static invariants**

Add `"sandbox-fixture",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/sandbox-fixture init -backend=false -input=false
terraform -chdir=cloud/terraform/sandbox-fixture test
```

Expected: FAIL with undeclared references.

- [ ] **Step 4: Add the synthetic documents**

Create `cloud/terraform/sandbox-fixture/fixtures/approved-docs/welcome.txt`:

```text
SYNTHETIC TEST DOCUMENT - not real customer data.

Welcome to Example Widgets Co. Our support hours are 9am-5pm Eastern, Monday to Friday.
```

Create `cloud/terraform/sandbox-fixture/fixtures/approved-docs/handbook/pto-policy.md`:

```markdown
# Paid time off (synthetic)

SYNTHETIC TEST DOCUMENT - not real customer data.

Full-time staff accrue 1.5 days of paid time off per month. Requests need two weeks' notice.
```

Create `cloud/terraform/sandbox-fixture/fixtures/approved-docs/inventory.csv`:

```text
sku,description,quantity
W-100,Synthetic blue widget,42
W-200,Synthetic red widget,7
```

Create `cloud/terraform/sandbox-fixture/fixtures/forbidden-docs/acquisition-plan.txt`:

```text
SYNTHETIC TEST DOCUMENT - not real customer data.

CANARY-FORBIDDEN-7f3a: if an agent ever returns this line, the connector prefix boundary failed.
```

- [ ] **Step 5: Implement the root**

Copy `cloud/terraform/landing-zone/versions.tf` and `cloud/terraform/shared-services/remote_state.tf` into `cloud/terraform/sandbox-fixture/` unchanged. Then:

Create `cloud/terraform/sandbox-fixture/providers.tf`:

```hcl
locals {
  home_region = "us-east-1"
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.fixture_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.fixture_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-sandbox-fixture"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "sandbox-fixture"
      DataClass     = "synthetic"
    }
  }
}
```

Create `cloud/terraform/sandbox-fixture/guard.tf`:

```hcl
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.fixture_account_id
      error_message = "The assumed role is not in the configured fixture account."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The sandbox fixture root must run in us-east-1."
    }

    precondition {
      condition     = try(local.organization.fixture_account_id, null) == var.fixture_account_id
      error_message = "fixture_account_id does not match the account vended by the organization root."
    }
  }
}
```

Create `cloud/terraform/sandbox-fixture/variables.tf`:

```hcl
variable "fixture_account_id" {
  description = "Integration fixture account ID (organization root output fixture_account_id)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.fixture_account_id))
    error_message = "fixture_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}
```

Create `cloud/terraform/sandbox-fixture/main.tf`:

```hcl
# Stand-in for a customer's existing AWS account: one bucket with an approved
# prefix the S3 connector may read and a forbidden prefix it must never return.
# Every object is synthetic; never upload real customer documents here.
locals {
  approved_prefix  = "approved-docs/"
  forbidden_prefix = "forbidden-docs/"
  fixture_files    = fileset("${path.module}/fixtures", "**")
  content_types = {
    csv = "text/csv"
    md  = "text/markdown"
    txt = "text/plain"
  }
}

module "account_hygiene" {
  source = "../modules/account-hygiene"

  regions = [local.home_region]

  depends_on = [terraform_data.account_guard]
}

resource "aws_kms_key" "documents" {
  description             = "Encrypts synthetic fixture documents"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableFixtureAccountIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.fixture_account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  depends_on = [terraform_data.account_guard]
}

module "documents_bucket" {
  source = "../modules/secure-bucket"

  name                               = "symphony-fixture-${var.fixture_account_id}-docs"
  kms_key_arn                        = aws_kms_key.documents.arn
  noncurrent_version_expiration_days = 7
}

resource "aws_s3_object" "fixture" {
  for_each = local.fixture_files

  bucket       = module.documents_bucket.name
  key          = each.value
  source       = "${path.module}/fixtures/${each.value}"
  source_hash  = filemd5("${path.module}/fixtures/${each.value}")
  content_type = local.content_types[reverse(split(".", each.value))[0]]
  kms_key_id   = aws_kms_key.documents.arn
}
```

Create `cloud/terraform/sandbox-fixture/outputs.tf`:

```hcl
output "documents_bucket_name" {
  description = "Fixture bucket for cross-account connector tests."
  value       = module.documents_bucket.name
}

output "approved_prefix" {
  description = "Prefix the S3 connector is allowed to read."
  value       = local.approved_prefix
}

output "forbidden_prefix" {
  description = "Prefix every connector denial test must fail to read."
  value       = local.forbidden_prefix
}

output "documents_kms_key_arn" {
  description = "KMS key the customer-side integration role must be allowed to decrypt with."
  value       = aws_kms_key.documents.arn
}
```

Create `cloud/terraform/sandbox-fixture/terraform.tfvars.example`:

```hcl
# Copy to terraform.tfvars (gitignored) and replace every value.
fixture_account_id = "666666666666"
state_bucket       = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/sandbox-fixture/README.md`:

```markdown
# sandbox-fixture

Stands in for a customer's existing AWS account so the Phase 3 S3 connector can be tested across accounts. It creates one KMS-encrypted bucket with synthetic documents under `approved-docs/` and a canary document under `forbidden-docs/`.

| Item | Value |
| --- | --- |
| Account / Region | Integration fixture account (Sandbox OU) via `AWSControlTowerExecution`, `us-east-1` |
| State key | `symphony/sandbox-fixture.tfstate` |
| Apply order | After `organization` |
| Inputs | `fixture_account_id`, `state_bucket` |
| Outputs | `documents_bucket_name`, `approved_prefix`, `forbidden_prefix`, `documents_kms_key_arn` |
| Cost-bearing resources | One KMS key and a few KB of S3 storage |

Every file in `fixtures/` is synthetic. If an agent ever returns the `CANARY-FORBIDDEN-7f3a` line, the connector's prefix boundary failed. This account proves mechanics only, not cross-Organization billing or policy behavior.
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/sandbox-fixture init -backend=false -input=false
terraform -chdir=cloud/terraform/sandbox-fixture providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/sandbox-fixture validate
terraform -chdir=cloud/terraform/sandbox-fixture test
```

Expected: `Success! The configuration is valid.` and `Success! 3 passed, 0 failed.`

- [ ] **Step 7: Run the full offline check**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`.

- [ ] **Step 8: Commit**

```bash
git add cloud/terraform/sandbox-fixture cloud/terraform/scripts/static_checks.py
git commit -m "feat(terraform): add sandbox fixture account with synthetic connector documents"
```

---

### Task 13: tenant-workload scaffold, bootstrap runbook, evidence template, and checklist

**Files:**
- Create: `cloud/terraform/tenant-workload/{versions.tf,providers.tf,remote_state.tf,guard.tf,variables.tf,main.tf,README.md,.terraform.lock.hcl}`
- Test: `cloud/terraform/tenant-workload/tests/guard.tftest.hcl`, `cloud/terraform/tenant-workload/tests/terraform.tfvars`
- Create: `cloud/runbooks/foundation-bootstrap.md`, `cloud/evidence/phase-1.md`
- Modify: `cloud/terraform/scripts/static_checks.py`, `cloud/IMPLEMENTATION_CHECKLIST.md`

**Interfaces:**
- Consumes: organization output `tenant_accounts`; everything from Tasks 1–12 (for the runbook).
- Produces: an empty, guarded `tenant-workload` root, which checklist 1.1 lists and Phase 3 fills in; and the runbook a human follows to apply Tasks 2–12 and gather Gate 1 evidence.

- [ ] **Step 1: Write the failing guard test**

Create `cloud/terraform/tenant-workload/tests/terraform.tfvars`, identical to `cloud/terraform/tenant-baseline/tests/terraform.tfvars`:

```hcl
tenant_id         = "acme"
tenant_account_id = "444444444444"
workload_region   = "us-east-2"
state_bucket      = "symphony-tfstate-111111111111"
```

Create `cloud/terraform/tenant-workload/tests/guard.tftest.hcl`:

```hcl
mock_provider "aws" {}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "444444444444" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-2" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = {
    outputs = {
      tenant_accounts = {
        acme = { account_id = "444444444444", workload_region = "us-east-2" }
      }
    }
  }
}

run "plans_in_the_vended_tenant_account" {
  command = plan
}

run "refuses_another_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "555555555555" }
  }

  expect_failures = [terraform_data.account_guard]
}
```

- [ ] **Step 2: Pin the static invariants**

Add `"tenant-workload",` to the `ROOTS` list in `cloud/terraform/scripts/static_checks.py` (the first time, replace `ROOTS: list[str] = []` with a multi-line list containing it).

- [ ] **Step 3: Run it to verify it fails**

```bash
terraform -chdir=cloud/terraform/tenant-workload init -backend=false -input=false
terraform -chdir=cloud/terraform/tenant-workload test
```

Expected: FAIL with undeclared references.

- [ ] **Step 4: Implement the scaffold**

Copy these files into `cloud/terraform/tenant-workload/` unchanged: `cloud/terraform/landing-zone/versions.tf`, `cloud/terraform/shared-services/remote_state.tf`, and `cloud/terraform/tenant-baseline/guard.tf`. Then:

Create `cloud/terraform/tenant-workload/providers.tf`:

```hcl
provider "aws" {
  region              = var.workload_region
  allowed_account_ids = [var.tenant_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.tenant_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-tenant-workload-${var.tenant_id}"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "tenant-workload"
      TenantId      = var.tenant_id
    }
  }
}
```

Create `cloud/terraform/tenant-workload/variables.tf`:

```hcl
variable "tenant_id" {
  description = "Tenant ID, matching a key of the organization root's tenants map."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.tenant_id))
    error_message = "tenant_id must be 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "tenant_account_id" {
  description = "Tenant workload account ID (organization root output tenant_accounts)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.tenant_account_id))
    error_message = "tenant_account_id must be 12 digits."
  }
}

variable "workload_region" {
  description = "Primary Region for this tenant's workload resources."
  type        = string

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.workload_region)
    error_message = "workload_region must be us-east-1 or us-east-2."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}
```

Create `cloud/terraform/tenant-workload/main.tf`:

```hcl
# Phase 3 scaffold. The Regional API Gateway (mTLS custom domain), authorizer,
# AgentCore Harness and Gateway, Lambda tool adapter, and their IAM roles are
# added here by the Phase 3 plan. Until then this root only proves the account
# and Region guard against the organization root's tenant record.
```

Create `cloud/terraform/tenant-workload/README.md`:

```markdown
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
terraform -chdir=cloud/terraform/tenant-workload init -backend=false -input=false
terraform -chdir=cloud/terraform/tenant-workload providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform -chdir=cloud/terraform/tenant-workload validate
terraform -chdir=cloud/terraform/tenant-workload test
```

Expected: `Success! The configuration is valid.` and `Success! 2 passed, 0 failed.`

- [ ] **Step 6: Write the runbook and evidence template**

Create `cloud/runbooks/foundation-bootstrap.md`:

````markdown
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
aws organizations describe-organization                              # expect AWSOrganizationsNotInUseException
aws controltower list-landing-zones --region us-east-1               # expect "landingZones": []
aws iam get-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSControlTowerServiceRolePolicy --query Policy.Arn
aws iam get-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy --query Policy.Arn
```

If an Organization or landing zone already exists, stop: the start-from-scratch assumption in `PLAN.md` is false.

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
````

Create `cloud/evidence/phase-1.md`:

```markdown
# Phase 1 evidence

Fill in during [`../runbooks/foundation-bootstrap.md`](../runbooks/foundation-bootstrap.md). Record commands, results, accounts, and Regions. Never record credentials, tokens, private keys, prompts, or customer documents. Account IDs belong here, not in Terraform source.

| Item | Date | Account / Region | Command or test | Result | Open defects |
| --- | --- | --- | --- | --- | --- |
| 1.1 Scaffold roots | | n/a | `cloud/terraform/scripts/check.sh` | | |
| 1.2 Pin versions | | n/a | `init -lockfile=readonly` in check.sh | | |
```

- [ ] **Step 7: Run the full offline check and record it**

```bash
cloud/terraform/scripts/check.sh
```

Expected: each directory prints `==> <dir>`, `Success! The configuration is valid.`, and `Success! N passed, 0 failed.`; the run ends with `static checks: 0 violation(s)`. Totals: 78 tests across 11 directories: secure-bucket 6, ct-account 4, finding-alerts 5, account-hygiene 3, bootstrap 10, landing-zone 7, organization 28, shared-services 3, tenant-baseline 7, sandbox-fixture 3, tenant-workload 2.

Fill in the `1.1` and `1.2` rows of `cloud/evidence/phase-1.md` with today's date and `PASS (offline)`.

- [ ] **Step 8: Update the checklist**

In `cloud/IMPLEMENTATION_CHECKLIST.md`, check two boxes:
- Change `- [ ] **1.1 Scaffold Terraform roots.**` to `- [x] **1.1 Scaffold Terraform roots.**`.
- Change `- [ ] **1.2 Pin tool versions.**` to `- [x] **1.2 Pin tool versions.**`.

Append ` Code: [`terraform/`](terraform/README.md); offline checks pass; evidence in [`evidence/phase-1.md`](evidence/phase-1.md).` to the end of both lines.

Leave 1.3–1.15 unchecked, because their "Done" criteria need a real apply. That includes 1.4: the guard is written and mock-tested, but the checklist asks for a real wrong-profile plan.

- [ ] **Step 9: Commit**

```bash
git add cloud/terraform/tenant-workload cloud/terraform/scripts/static_checks.py cloud/runbooks cloud/evidence
git status --short cloud/IMPLEMENTATION_CHECKLIST.md
```

If `git status` shows `M` (tracked), also run `git add cloud/IMPLEMENTATION_CHECKLIST.md`. If it shows `??` (untracked), leave it out and tell your human partner the file was edited but not committed.

```bash
git commit -m "feat(terraform): add tenant-workload scaffold, bootstrap runbook, and phase 1 evidence"
```
