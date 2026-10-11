data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_caller_identity" "audit" {
  provider = aws.audit
}

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
      error_message = "The organization root must run in us-east-1."
    }

    precondition {
      condition     = data.aws_caller_identity.audit.account_id == var.audit_account_id
      error_message = "The audit provider did not reach the configured audit account."
    }

    precondition {
      condition     = local.bootstrap.management_account_id == var.management_account_id && local.bootstrap.audit_account_id == var.audit_account_id
      error_message = "Bootstrap state does not match the configured management and audit accounts."
    }

    precondition {
      condition     = try(length(local.landing_zone.landing_zone_arn) > 0, false)
      error_message = "Apply the landing-zone root before the organization root."
    }
  }
}
