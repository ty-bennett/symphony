variable "fixture_account_id" {
  description = "Account that owns the documents (organization root output fixture_account_id)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.fixture_account_id))
    error_message = "fixture_account_id must be 12 digits."
  }
}

variable "tenant_id" {
  description = "Tenant whose document tool may assume the integration role."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.tenant_id))
    error_message = "tenant_id must be 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}
