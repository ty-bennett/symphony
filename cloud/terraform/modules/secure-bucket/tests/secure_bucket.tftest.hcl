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

run "expires_objects_under_a_prefix_when_asked" {
  command = plan

  variables {
    expiration = { prefix = "recordings/", days = 30 }
  }

  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.this.rule) == 2 && one([for r in aws_s3_bucket_lifecycle_configuration.this.rule : one(r.expiration).days if r.id == "expire-prefix"]) == 30
    error_message = "An expiration rule must be added in the same lifecycle configuration."
  }
}

run "adds_no_expiration_by_default" {
  command = plan

  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.this.rule) == 1
    error_message = "Objects must not expire unless requested."
  }
}
