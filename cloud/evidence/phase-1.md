# Phase 1 evidence

Fill in during [`../runbooks/foundation-bootstrap.md`](../runbooks/foundation-bootstrap.md). Record commands, results, accounts, and Regions. Never record credentials, tokens, private keys, prompts, or customer documents. Account IDs belong here, not in Terraform source.

| Item | Date | Account / Region | Command or test | Result | Open defects |
| --- | --- | --- | --- | --- | --- |
| 1.1 Scaffold roots | 2026-10-11 | n/a | `cloud/terraform/scripts/check.sh` | PASS (offline): 78 tests, 11 dirs, 0 static violations | |
| 1.2 Pin versions | 2026-10-11 | n/a | `init -lockfile=readonly` in check.sh | PASS (offline): Terraform 1.16.4, hashicorp/aws 6.68.0 locked for darwin_arm64 + linux_amd64 | |
| 1.3 State backend | 2026-10-11 | 695911680545 / us-east-1 | bootstrap apply + migrate; replan exit 0 | PASS | |
| 1.5 Organization | 2026-10-11 | 695911680545 | bootstrap import of o-r2xq6g9vo2 | PASS (adopted; 1 import, 0 destroy) | |
| 1.6 Security accounts | 2026-10-11 | 728330702611, 520168725155 | list-accounts-for-parent Security OU | PASS | |
| 1.7 Control Tower | 2026-10-11 | 695911680545 / us-east-1 | get-landing-zone | PASS: ACTIVE, IN_SYNC, 4.0 | |
| 1.7a OUs registered | 2026-10-11 | 3 OUs | list-enabled-baselines | PASS: 5.0 SUCCEEDED x3 | |
| 1.8–1.10 Vended accounts | 2026-10-11 | 130237968226, 603238661867, 719453899809 | organization apply | PASS | |
| 1.12 Delegated security | 2026-10-11 | 520168725155 / us-east-1 | create-sample-findings → EventBridge → SNS email | PASS: email received | |
| 1.13 Human access | 2026-10-11 | Identity Center | access portal sign-in with MFA | PASS (user-confirmed) | |
| 1.14 Budget | 2026-10-11 | demo tenant | describe-notifications-for-budget | Configured: 80% forecast/80%/100% actual; delivery test not yet run | |
| 1.15 Repeatability | 2026-10-11 | all roots | plan -detailed-exitcode | PASS: exit 0 everywhere | |
