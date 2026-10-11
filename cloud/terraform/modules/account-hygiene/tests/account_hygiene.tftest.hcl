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
