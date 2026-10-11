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

run "encrypts_tenant_data_with_a_rotating_key" {
  command = plan

  assert {
    condition     = aws_kms_key.tenant_data.enable_key_rotation
    error_message = "The tenant data key must rotate."
  }

  assert {
    condition     = jsondecode(aws_kms_key.tenant_data.policy).Statement[1].Principal.Service == "logs.us-east-2.amazonaws.com"
    error_message = "CloudWatch Logs in the workload Region must be able to use the key."
  }

  assert {
    condition     = aws_kms_alias.tenant_data.name == "alias/symphony/acme/data"
    error_message = "The key alias must include the tenant ID."
  }
}

run "protects_the_device_registry" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.device_registry.hash_key == "cert_serial"
    error_message = "Registry items are keyed by certificate serial."
  }

  assert {
    condition     = one(one([for i in aws_dynamodb_table.device_registry.global_secondary_index : i if i.name == "by_device_id"]).key_schema).attribute_name == "device_id"
    error_message = "The by_device_id index must be keyed by device_id for rotation lookups."
  }

  assert {
    condition     = aws_dynamodb_table.device_registry.deletion_protection_enabled
    error_message = "The device registry must have deletion protection."
  }

  assert {
    condition     = one(aws_dynamodb_table.device_registry.point_in_time_recovery).enabled
    error_message = "The device registry needs point-in-time recovery."
  }
}

run "requires_mfa_and_admin_created_users" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.users.mfa_configuration == "ON"
    error_message = "Pilot users must use MFA."
  }

  assert {
    condition     = one(aws_cognito_user_pool.users.admin_create_user_config).allow_admin_create_user_only
    error_message = "Self sign-up must be disabled."
  }

  assert {
    condition     = one(aws_cognito_user_pool.users.user_attribute_update_settings).attributes_require_verification_before_update == toset(["email"])
    error_message = "Email changes must be verified before they take effect."
  }

  assert {
    condition     = aws_cognito_user_pool.users.deletion_protection == "ACTIVE"
    error_message = "The user pool must have deletion protection."
  }
}

run "keeps_application_logs_short_lived_and_encrypted" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.application.name == "/symphony/acme/application" && aws_cloudwatch_log_group.application.retention_in_days == 30
    error_message = "Application logs must use the tenant log group and 30-day default retention."
  }
}

run "refuses_a_tenant_id_the_organization_did_not_vend" {
  command = plan

  variables {
    tenant_id = "globex"
  }

  expect_failures = [terraform_data.account_guard]
}

run "refuses_a_region_that_differs_from_the_organization_record" {
  command = plan

  variables {
    workload_region = "us-east-1"
  }

  override_data {
    target = data.aws_region.current
    values = { region = "us-east-1" }
  }

  expect_failures = [terraform_data.account_guard]
}

run "rejects_a_region_outside_the_pilot_pair" {
  command = plan

  variables {
    workload_region = "us-west-2"
  }

  expect_failures = [var.workload_region]
}
