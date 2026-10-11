mock_provider "aws" {}

variables {
  fixture_account_id = "666666666666"
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

run "uploads_approved_and_forbidden_synthetic_documents" {
  command = plan

  assert {
    condition = toset(keys(aws_s3_object.fixture)) == toset([
      "approved-docs/welcome.txt",
      "approved-docs/handbook/pto-policy.md",
      "approved-docs/inventory.csv",
      "forbidden-docs/acquisition-plan.txt",
    ])
    error_message = "Fixture objects must match the files under fixtures/."
  }

  assert {
    condition     = alltrue([for key in keys(aws_s3_object.fixture) : startswith(key, "approved-docs/") || startswith(key, "forbidden-docs/")])
    error_message = "Every fixture object must sit under the approved or forbidden prefix."
  }
}

run "labels_objects_and_names_the_bucket" {
  command = plan

  assert {
    condition     = alltrue([for o in aws_s3_object.fixture : o.content_type != null])
    error_message = "Every fixture object needs a text content type."
  }

  assert {
    condition     = module.documents_bucket.name == "symphony-fixture-666666666666-docs"
    error_message = "Fixture bucket name must include the fixture account ID."
  }
}

run "refuses_a_tenant_account" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "444444444444" }
  }

  expect_failures = [terraform_data.account_guard]
}
