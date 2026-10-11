variable "tenant_id" {
  description = "Tenant ID, matching a key of the organization root's tenants map."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.tenant_id))
    error_message = "tenant_id must be 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "tenant_account_id" {
  description = "Tenant workload account ID (organization root output tenant_accounts)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.tenant_account_id))
    error_message = "tenant_account_id must be 12 digits."
  }
}

variable "workload_region" {
  description = "Primary Region for this tenant's workload resources."
  type        = string

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.workload_region)
    error_message = "workload_region must be us-east-1 or us-east-2."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for tenant application log groups."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs retention value."
  }
}
