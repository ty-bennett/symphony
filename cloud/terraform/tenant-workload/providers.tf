provider "aws" {
  region              = var.workload_region
  allowed_account_ids = [var.tenant_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.tenant_account_id}:role/AWSControlTowerExecution"
    session_name = "symphony-tenant-workload-${var.tenant_id}"
  }

  default_tags {
    tags = {
      Project       = "symphony"
      ManagedBy     = "terraform"
      TerraformRoot = "tenant-workload"
      TenantId      = var.tenant_id
    }
  }
}
