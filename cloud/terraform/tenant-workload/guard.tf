data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  vended_tenant = try(local.organization.tenant_accounts[var.tenant_id], null)
}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.tenant_account_id
      error_message = "The assumed role is not in the configured tenant account."
    }

    precondition {
      condition     = data.aws_region.current.region == var.workload_region
      error_message = "The provider Region does not match workload_region."
    }

    precondition {
      condition     = try(local.vended_tenant.account_id, null) == var.tenant_account_id
      error_message = "tenant_id and tenant_account_id do not match an account vended by the organization root."
    }

    precondition {
      condition     = try(local.vended_tenant.workload_region, null) == var.workload_region
      error_message = "workload_region differs from the Region recorded for this tenant in the organization root."
    }
  }
}
