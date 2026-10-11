locals {
  home_region     = "us-east-1"
  allowed_regions = ["us-east-1", "us-east-2"]
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "organization"
    }
  }
}

# Delegated security administration runs inside the audit account.
provider "aws" {
  alias               = "audit"
  region              = local.home_region
  allowed_account_ids = [var.audit_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.audit_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-organization"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "organization"
    }
  }
}
