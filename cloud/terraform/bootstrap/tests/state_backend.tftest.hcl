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
