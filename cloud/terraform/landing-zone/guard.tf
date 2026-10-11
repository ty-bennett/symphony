data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials, Region, or upstream state are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.management_account_id
      error_message = "Credentials are not for the configured management account. Check AWS_PROFILE."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The landing zone root must run in us-east-1."
    }

    precondition {
      condition     = local.bootstrap.management_account_id == var.management_account_id
      error_message = "Bootstrap state belongs to a different management account."
    }
  }
}
