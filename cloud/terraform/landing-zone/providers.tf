locals {
  home_region      = "us-east-1"
  governed_regions = ["us-east-1", "us-east-2"]
}

provider "aws" {
  region              = local.home_region
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "landing-zone"
    }
  }
}
