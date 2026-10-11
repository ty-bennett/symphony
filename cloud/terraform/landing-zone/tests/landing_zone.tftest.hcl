mock_provider "aws" {}

variables {
  management_account_id = "111111111111"
  state_bucket          = "symphony-tfstate-111111111111"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111111111111" }
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
      log_archive_account_id = "222222222222"
      audit_account_id       = "333333333333"
    }
  }
}

override_resource {
  target          = aws_kms_key.controltower_logs
  override_during = plan
  values          = { arn = "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111" }
}

run "uses_landing_zone_version_4" {
  command = plan

  assert {
    condition     = aws_controltower_landing_zone.this.version == "4.0"
    error_message = "Default landing zone version must be 4.0."
  }
}

run "governs_only_the_two_pilot_regions" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).governedRegions == ["us-east-1", "us-east-2"]
    error_message = "Governed Regions must be exactly us-east-1 and us-east-2."
  }
}

run "routes_logs_and_security_to_the_shared_accounts" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.accountId == "222222222222"
    error_message = "Centralized logging must target the log archive account."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).securityRoles.accountId == "333333333333"
    error_message = "Security roles must target the audit account."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).config.accountId == "333333333333"
    error_message = "The Config aggregator must be the audit account."
  }
}

run "sets_every_enabled_flag_explicitly" {
  command = plan

  assert {
    condition = alltrue([
      jsondecode(aws_controltower_landing_zone.this.manifest_json).accessManagement.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).config.enabled,
      jsondecode(aws_controltower_landing_zone.this.manifest_json).securityRoles.enabled,
      !jsondecode(aws_controltower_landing_zone.this.manifest_json).backup.enabled,
    ])
    error_message = "Access management, logging, Config, and security roles are on; backup is off."
  }
}

run "encrypts_logs_with_the_symphony_key" {
  command = plan

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).centralizedLogging.configurations.kmsKeyArn == "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111"
    error_message = "CloudTrail logs must use the Symphony KMS key."
  }

  assert {
    condition     = jsondecode(aws_controltower_landing_zone.this.manifest_json).config.configurations.kmsKeyArn == "arn:aws:kms:us-east-1:111111111111:key/11111111-1111-1111-1111-111111111111"
    error_message = "Config logs must use the Symphony KMS key."
  }

  assert {
    condition     = jsondecode(aws_kms_key.controltower_logs.policy).Statement[2].Condition.StringEquals["aws:SourceArn"] == "arn:aws:cloudtrail:us-east-1:111111111111:trail/aws-controltower-BaselineCloudTrail"
    error_message = "CloudTrail may use the key only for the Control Tower baseline trail."
  }
}

run "refuses_bootstrap_state_from_another_account" {
  command = plan

  override_data {
    target = data.terraform_remote_state.bootstrap
    values = {
      outputs = {
        management_account_id  = "999999999999"
        log_archive_account_id = "222222222222"
        audit_account_id       = "333333333333"
      }
    }
  }

  expect_failures = [terraform_data.account_guard]
}

run "rejects_a_3x_version" {
  command = plan

  variables {
    landing_zone_version = "3.3"
  }

  expect_failures = [var.landing_zone_version]
}
