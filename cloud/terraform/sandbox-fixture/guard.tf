data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.fixture_account_id
      error_message = "The assumed role is not in the configured fixture account."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The sandbox fixture root must run in us-east-1."
    }

    precondition {
      condition     = try(local.organization.fixture_account_id, null) == var.fixture_account_id
      error_message = "fixture_account_id does not match the account vended by the organization root."
    }
  }
}
