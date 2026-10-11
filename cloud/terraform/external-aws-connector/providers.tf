locals {
  home_region = "us-east-1"
}

# Applied in the customer's existing AWS account. For the pilot that is the
# Symphony integration fixture account; a real customer applies this template
# (or its CloudFormation equivalent) with their own credentials.
provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.fixture_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.fixture_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-external-aws-connector"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "external-aws-connector"
      TenantId      = var.tenant_id
    }
  }
}
