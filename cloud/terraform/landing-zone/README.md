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
