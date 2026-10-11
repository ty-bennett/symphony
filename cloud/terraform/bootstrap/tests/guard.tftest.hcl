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
