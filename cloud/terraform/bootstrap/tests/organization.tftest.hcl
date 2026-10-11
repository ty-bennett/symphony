mock_provider "aws" {}

variables {
  management_account_id     = "111111111111"
  log_archive_account_email = "aws+log-archive@example.com"
  audit_account_email       = "aws+audit@example.com"
  existing_organization_id  = null # keep tests independent of a local terraform.tfvars
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_resource {
  target          = aws_organizations_organization.this
  override_during = plan
  values = {
    id    = "o-exampleorg1"
    roots = [{ id = "r-abcd", arn = "arn:aws:organizations::111111111111:root/o-exampleorg1/r-abcd", name = "Root", policy_types = [] }]
  }
}

override_resource {
  target          = aws_organizations_organizational_unit.security
  override_during = plan
  values          = { id = "ou-abcd-security" }
}

run "creates_an_all_features_organization" {
  command = plan

  assert {
    condition     = aws_organizations_organization.this.feature_set == "ALL"
    error_message = "Control Tower requires all features."
  }
}

run "places_shared_accounts_in_the_security_ou" {
  command = plan

  assert {
    condition     = aws_organizations_organizational_unit.security.parent_id == "r-abcd"
    error_message = "The Security OU must sit directly under the root."
  }

  assert {
    condition     = aws_organizations_account.log_archive.parent_id == "ou-abcd-security" && aws_organizations_account.audit.parent_id == "ou-abcd-security"
    error_message = "Both service integration accounts must be in the Security OU."
  }

  assert {
    condition     = !aws_organizations_account.log_archive.close_on_deletion && !aws_organizations_account.audit.close_on_deletion
    error_message = "Removing an account from Terraform must never close it."
  }
}

run "creates_controltower_service_roles" {
  command = plan

  assert {
    condition     = toset(keys(aws_iam_role.controltower)) == toset(["AWSControlTowerAdmin", "AWSControlTowerCloudTrailRole", "AWSControlTowerStackSetRole"])
    error_message = "Exactly the three landing zone 4.0 service roles are required."
  }

  assert {
    condition     = alltrue([for role in aws_iam_role.controltower : role.path == "/service-role/"])
    error_message = "Control Tower service roles must use the /service-role/ path."
  }

  assert {
    condition     = jsondecode(aws_iam_role.controltower["AWSControlTowerAdmin"].assume_role_policy).Statement[0].Principal.Service == "controltower.amazonaws.com"
    error_message = "AWSControlTowerAdmin must trust only Control Tower."
  }

  assert {
    condition     = aws_iam_role_policy_attachment.controltower["AWSControlTowerCloudTrailRole/AWSControlTowerCloudTrailRolePolicy"].policy_arn == "arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy"
    error_message = "The CloudTrail role must use the AWS managed policy."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.controltower_stackset.policy).Statement[0].Resource == "arn:aws:iam::*:role/AWSControlTowerExecution"
    error_message = "The StackSet role may assume only AWSControlTowerExecution."
  }
}

run "rejects_reused_account_emails" {
  command = plan

  variables {
    audit_account_email = "AWS+Log-Archive@example.com"
  }

  expect_failures = [var.audit_account_email]
}

run "adopts_an_existing_organization" {
  command = plan

  variables {
    existing_organization_id = "o-r2xq6g9vo2"
  }

  assert {
    condition     = aws_organizations_organization.this.feature_set == "ALL"
    error_message = "An adopted organization must still require all features."
  }
}

run "rejects_a_malformed_organization_id" {
  command = plan

  variables {
    existing_organization_id = "r-abcd"
  }

  expect_failures = [var.existing_organization_id]
}
