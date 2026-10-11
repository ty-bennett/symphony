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
