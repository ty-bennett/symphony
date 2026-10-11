data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Fails the plan before any resource changes when credentials or Region are wrong.
resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.management_account_id
      error_message = "Credentials are not for the configured management account. Check AWS_PROFILE."
    }

    precondition {
      condition     = data.aws_region.current.region == local.home_region
      error_message = "The bootstrap root must run in us-east-1."
    }
  }
}
