locals {
  home_region = "us-east-1"
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.shared_services_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.shared_services_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-shared-services"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "shared-services"
    }
  }
}
