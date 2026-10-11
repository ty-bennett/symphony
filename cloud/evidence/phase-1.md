# Phase 1 evidence

Fill in during [`../runbooks/foundation-bootstrap.md`](../runbooks/foundation-bootstrap.md). Record commands, results, accounts, and Regions. Never record credentials, tokens, private keys, prompts, or customer documents. Account IDs belong here, not in Terraform source.

| Item | Date | Account / Region | Command or test | Result | Open defects |
| --- | --- | --- | --- | --- | --- |
| 1.1 Scaffold roots | 2026-10-11 | n/a | `cloud/terraform/scripts/check.sh` | PASS (offline): 78 tests, 11 dirs, 0 static violations | |
| 1.2 Pin versions | 2026-10-11 | n/a | `init -lockfile=readonly` in check.sh | PASS (offline): Terraform 1.16.4, hashicorp/aws 6.68.0 locked for darwin_arm64 + linux_amd64 | |
