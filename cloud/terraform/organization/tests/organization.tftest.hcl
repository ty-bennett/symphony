mock_provider "aws" {
  override_during = plan

  # Every vended account reports the same placeholder ID during plan.
  mock_resource "aws_servicecatalog_provisioned_product" {
    defaults = {
      outputs = [{ key = "AccountId", value = "444444444444", description = "" }]
    }
  }
}

mock_provider "aws" {
  alias           = "audit"
  override_during = plan
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_caller_identity.audit
  values = { account_id = "333333333333" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      management_account_id  = "111111111111"
      audit_account_id       = "333333333333"
      log_archive_account_id = "222222222222"
      organization_root_id   = "r-abcd"
    }
  }
}

override_data {
  target = data.terraform_remote_state.landing_zone
  values = { outputs = { landing_zone_arn = "arn:aws:controltower:us-east-1:111111111111:landingzone/EXAMPLE" } }
}

# --- ous ---
run "creates_three_workload_ous_under_the_root" {
  command = plan

  assert {
    condition     = toset([for ou in aws_organizations_organizational_unit.workload : ou.name]) == toset(["Infrastructure", "Customers", "Sandbox"])
    error_message = "Infrastructure, Customers, and Sandbox OUs are required."
  }

  assert {
    condition     = alltrue([for ou in aws_organizations_organizational_unit.workload : ou.parent_id == "r-abcd"])
    error_message = "Workload OUs must sit directly under the root."
  }
}

run "registers_every_workload_ou_with_control_tower" {
  command = plan

  assert {
    condition     = length(aws_controltower_baseline.workload) == 3
    error_message = "Each workload OU needs an AWSControlTowerBaseline."
  }

  assert {
    condition     = alltrue([for b in aws_controltower_baseline.workload : one(b.parameters).value == var.identity_center_enabled_baseline_arn])
    error_message = "Each baseline must reference the Identity Center enabled baseline."
  }
}

run "refuses_to_run_before_the_landing_zone_exists" {
  command = plan

  override_data {
    target = data.terraform_remote_state.landing_zone
    values = { outputs = {} }
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_the_wrong_audit_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.audit
    values = { account_id = "999999999999" }
  }

  expect_failures = [terraform_data.account_guard]
}
