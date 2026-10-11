mock_provider "aws" {}

variables {
  account_name             = "Customer Acme"
  account_email            = "aws+acme@example.com"
  provisioned_product_name = "symphony-tenant-acme"
  organizational_unit      = { id = "ou-abcd-customers", name = "Customers" }
  sso_user                 = { email = "ops@example.com", first_name = "Symphony", last_name = "Operations" }
}

run "passes_every_account_factory_parameter" {
  command = plan

  assert {
    condition = toset([for p in aws_servicecatalog_provisioned_product.this.provisioning_parameters : p.key]) == toset([
      "AccountName", "AccountEmail", "ManagedOrganizationalUnit", "SSOUserEmail", "SSOUserFirstName", "SSOUserLastName",
    ])
    error_message = "Account Factory needs exactly its six provisioning parameters."
  }
}

run "formats_the_managed_ou_as_name_and_id" {
  command = plan

  assert {
    condition     = one([for p in aws_servicecatalog_provisioned_product.this.provisioning_parameters : p.value if p.key == "ManagedOrganizationalUnit"]) == "Customers (ou-abcd-customers)"
    error_message = "ManagedOrganizationalUnit must be 'Name (ou-id)'."
  }
}

run "exposes_the_vended_account_id" {
  command = plan

  override_resource {
    target          = aws_servicecatalog_provisioned_product.this
    override_during = plan
    values = {
      outputs = [
        { key = "AccountEmail", value = "aws+acme@example.com", description = "" },
        { key = "AccountId", value = "444444444444", description = "" },
      ]
    }
  }

  assert {
    condition     = output.account_id == "444444444444"
    error_message = "account_id must come from the AccountId provisioned product output."
  }
}

run "rejects_spaces_in_the_product_name" {
  command = plan

  variables {
    provisioned_product_name = "symphony tenant acme"
  }

  expect_failures = [var.provisioned_product_name]
}
