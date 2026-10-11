locals {
  home_region = "us-east-1"
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "bootstrap"
    }
  }
}
