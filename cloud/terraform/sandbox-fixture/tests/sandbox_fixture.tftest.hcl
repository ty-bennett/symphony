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
      "approved-docs/sales-2026-q3.csv",
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

run "grants_the_knowledge_base_only_approved_docs" {
  command = plan

  variables {
    knowledge_base_reader_role_arns = ["arn:aws:iam::444444444444:role/symphony-acme-knowledge-base"]
  }

  assert {
    condition     = strcontains(jsonencode(jsondecode(aws_kms_key.documents.policy).Statement[1]), "kms:ViaService") && jsondecode(aws_kms_key.documents.policy).Statement[1].Action == "kms:Decrypt"
    error_message = "The knowledge base may only decrypt through S3."
  }
}

run "grants_nothing_by_default" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_kms_key.documents.policy).Statement) == 1
    error_message = "Without reader roles, the fixture key trusts only its own account."
  }
}
