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
