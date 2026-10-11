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

# --- scps ---
run "denies_regions_outside_the_pilot_pair" {
  command = plan

  assert {
    condition     = jsondecode(aws_organizations_policy.scp["region-guardrail"].content).Statement[0].Condition.StringNotEquals["aws:RequestedRegion"] == ["us-east-1", "us-east-2"]
    error_message = "Only us-east-1 and us-east-2 may be allowed."
  }

  assert {
    condition     = !contains(jsondecode(aws_organizations_policy.scp["region-guardrail"].content).Statement[0].NotAction, "bedrock:*")
    error_message = "Bedrock must stay Region-restricted; it is not a global service."
  }
}

run "protects_the_security_baseline" {
  command = plan

  assert {
    condition     = toset([for s in jsondecode(aws_organizations_policy.scp["baseline-protection"].content).Statement : s.Sid]) == toset(["DenyLeavingOrganization", "DenyRootUser", "DenyLongLivedIamUserCredentials", "DenySecurityBaselineChanges", "DenyKmsKeyDeletion"])
    error_message = "Baseline protection statements are missing or unexpected."
  }
}

run "fits_the_scp_size_limit" {
  command = plan

  assert {
    condition     = alltrue([for p in aws_organizations_policy.scp : length(p.content) <= 5120])
    error_message = "Each SCP must be at most 5,120 characters or AWS rejects it at apply time."
  }
}

run "attaches_both_policies_to_every_workload_ou" {
  command = plan

  assert {
    condition     = toset(keys(aws_organizations_policy_attachment.scp)) == toset(["region-guardrail/infrastructure", "region-guardrail/customers", "region-guardrail/sandbox", "baseline-protection/infrastructure", "baseline-protection/customers", "baseline-protection/sandbox"])
    error_message = "Both SCPs must be attached to Infrastructure, Customers, and Sandbox."
  }
}

# --- accounts ---
run "vends_one_account_per_tenant" {
  command = plan

  assert {
    condition     = toset(keys(module.tenant_account)) == toset(["acme"])
    error_message = "Each configured tenant needs exactly one vended account."
  }

  assert {
    condition     = aws_servicecatalog_principal_portfolio_association.terraform.principal_arn == "arn:aws:iam::111111111111:role/SymphonyTerraform"
    error_message = "The Terraform principal must be associated with the Account Factory portfolio."
  }
}

run "publishes_tenant_account_ids_and_regions" {
  command = plan

  assert {
    condition     = output.tenant_accounts["acme"].account_id == "444444444444"
    error_message = "tenant_accounts must expose the vended account ID."
  }

  assert {
    condition     = output.tenant_accounts["acme"].workload_region == "us-east-1"
    error_message = "tenant_accounts must expose the tenant workload Region."
  }
}

run "rejects_a_reused_account_email" {
  command = plan

  variables {
    fixture_account = { email = "AWS+Shared@example.com" }
  }

  expect_failures = [var.tenants]
}

run "rejects_a_region_outside_the_pilot_pair" {
  command = plan

  variables {
    tenants = {
      acme = {
        display_name       = "Acme Pilot"
        account_email      = "aws+acme@example.com"
        workload_region    = "us-west-2"
        monthly_budget_usd = 250
        billing_contacts   = ["billing@acme.example.com"]
      }
    }
  }

  expect_failures = [var.tenants]
}

run "rejects_an_invalid_tenant_id" {
  command = plan

  variables {
    tenants = {
      "Acme_Corp" = {
        display_name       = "Acme Pilot"
        account_email      = "aws+acme@example.com"
        workload_region    = "us-east-1"
        monthly_budget_usd = 250
        billing_contacts   = ["billing@acme.example.com"]
      }
    }
  }

  expect_failures = [var.tenants]
}

# --- security services ---
run "delegates_guardduty_to_audit_in_both_regions" {
  command = plan

  assert {
    condition     = toset([for a in aws_guardduty_organization_admin_account.this : "${a.region}/${a.admin_account_id}"]) == toset(["us-east-1/333333333333", "us-east-2/333333333333"])
    error_message = "GuardDuty must be delegated to the audit account in both governed Regions."
  }

  assert {
    condition     = alltrue([for c in aws_guardduty_organization_configuration.this : c.auto_enable_organization_members == "ALL"])
    error_message = "GuardDuty must auto-enable every member account."
  }
}

run "delegates_security_hub_without_extra_standards" {
  command = plan

  assert {
    condition     = toset([for a in aws_securityhub_organization_admin_account.this : "${a.region}/${a.admin_account_id}"]) == toset(["us-east-1/333333333333", "us-east-2/333333333333"])
    error_message = "Security Hub must be delegated to the audit account in both governed Regions."
  }

  assert {
    condition     = alltrue([for c in aws_securityhub_organization_configuration.this : c.auto_enable && c.auto_enable_standards == "NONE"])
    error_message = "Security Hub must auto-enable accounts without enabling paid standards."
  }
}

run "enables_trusted_access_for_both_services" {
  command = plan

  assert {
    condition     = toset([for s in aws_organizations_aws_service_access.security : s.service_principal]) == toset(["guardduty.amazonaws.com", "securityhub.amazonaws.com"])
    error_message = "GuardDuty and Security Hub need trusted access."
  }
}

run "creates_finding_alerts_in_each_region" {
  command = plan

  assert {
    condition     = toset(keys(module.finding_alerts)) == toset(["us-east-1", "us-east-2"])
    error_message = "Each governed Region needs a finding alert topic."
  }
}
