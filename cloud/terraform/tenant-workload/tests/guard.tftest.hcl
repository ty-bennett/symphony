mock_provider "aws" {}

mock_provider "archive" {}

mock_provider "random" {}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "444444444444" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-2" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = {
    outputs = {
      tenant_accounts = {
        acme = { account_id = "444444444444", workload_region = "us-east-2" }
      }
      fixture_account_id = "666666666666"
    }
  }
}

override_data {
  target = data.terraform_remote_state.tenant_baseline
  values = { outputs = { tenant_data_kms_key_arn = "arn:aws:kms:us-east-2:444444444444:key/22222222-2222-2222-2222-222222222222" } }
}

override_data {
  target = data.terraform_remote_state.sandbox_fixture
  values = { outputs = { documents_bucket_name = "symphony-fixture-666666666666-docs", approved_prefix = "approved-docs/" } }
}

run "plans_in_the_vended_tenant_account" {
  command = plan
}

run "refuses_another_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "555555555555" }
  }

  expect_failures = [terraform_data.account_guard]
}
