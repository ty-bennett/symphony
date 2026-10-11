mock_provider "aws" {}

variables {
  fixture_account_id = "666666666666"
  tenant_id          = "acme"
  state_bucket       = "symphony-tfstate-111111111111"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "666666666666" }
}

override_data {
  target = data.aws_region.current
  values = { region = "us-east-1" }
}

override_data {
  target = data.terraform_remote_state.organization
  values = { outputs = { fixture_account_id = "666666666666" } }
}

override_data {
  target = data.terraform_remote_state.sandbox_fixture
  values = {
    outputs = {
      documents_bucket_name = "symphony-fixture-666666666666-docs"
      approved_prefix       = "approved-docs/"
      documents_kms_key_arn = "arn:aws:kms:us-east-1:666666666666:key/33333333-3333-3333-3333-333333333333"
    }
  }
}

override_data {
  target = data.terraform_remote_state.tenant_workload
  values = {
    outputs = {
      documents_tool_role_arn = "arn:aws:iam::444444444444:role/symphony-acme-documents-tool"
      external_id             = "6f1c2c1e-0000-4000-8000-000000000000"
    }
  }
}

run "trusts_only_the_tenant_tool_role_with_the_external_id" {
  command = plan

  assert {
    condition     = aws_iam_role.integration.name == "SymphonyIntegrationRole"
    error_message = "The tenant tool expects SymphonyIntegrationRole."
  }

  assert {
    condition     = jsondecode(aws_iam_role.integration.assume_role_policy).Statement[0].Principal.AWS == "arn:aws:iam::444444444444:role/symphony-acme-documents-tool"
    error_message = "Only the exact tenant tool role may assume the integration role."
  }

  assert {
    condition     = jsondecode(aws_iam_role.integration.assume_role_policy).Statement[0].Condition.StringEquals["sts:ExternalId"] == "6f1c2c1e-0000-4000-8000-000000000000"
    error_message = "AssumeRole must require the tenant's external ID."
  }
}

run "reads_only_the_approved_prefix" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role_policy.integration.policy).Statement[0].Condition.StringLike["s3:prefix"] == ["approved-docs/", "approved-docs/*"]
    error_message = "Listing must be limited to the approved prefix."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.integration.policy).Statement[1].Resource == "arn:aws:s3:::symphony-fixture-666666666666-docs/approved-docs/*"
    error_message = "Reads must be limited to objects under the approved prefix."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.integration.policy, "forbidden-docs") && !strcontains(aws_iam_role_policy.integration.policy, "s3:Put") && !strcontains(aws_iam_role_policy.integration.policy, "s3:Delete")
    error_message = "No write, delete, or forbidden-prefix access."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.integration.policy).Statement[2].Condition.StringEquals["kms:ViaService"] == "s3.us-east-1.amazonaws.com"
    error_message = "KMS decrypt is allowed only through S3."
  }
}

run "refuses_a_non_fixture_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "444444444444" }
  }

  expect_failures = [terraform_data.account_guard]
}
