# sandbox-fixture

Stands in for a customer's existing AWS account so the Phase 3 S3 connector can be tested across accounts. It creates one KMS-encrypted bucket with synthetic documents under `approved-docs/` and a canary document under `forbidden-docs/`.

| Item | Value |
| --- | --- |
| Account / Region | Integration fixture account (Sandbox OU) via `AWSControlTowerExecution`, `us-east-1` |
| State key | `symphony/sandbox-fixture.tfstate` |
| Apply order | After `organization` |
| Inputs | `fixture_account_id`, `state_bucket`, `knowledge_base_reader_role_arns` (optional; tenant KB roles allowed to read `approved-docs/` and decrypt via S3) |
| Outputs | `documents_bucket_name`, `approved_prefix`, `forbidden_prefix`, `documents_kms_key_arn` |
| Cost-bearing resources | One KMS key and a few KB of S3 storage |

Every file in `fixtures/` is synthetic. If an agent ever returns the `CANARY-FORBIDDEN-7f3a` line, the connector's prefix boundary failed. This account proves mechanics only, not cross-Organization billing or policy behavior.
