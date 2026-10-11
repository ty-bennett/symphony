mock_provider "aws" {}

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
    }
  }
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
