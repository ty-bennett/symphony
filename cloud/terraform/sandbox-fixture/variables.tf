variable "fixture_account_id" {
  description = "Integration fixture account ID (organization root output fixture_account_id)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.fixture_account_id))
    error_message = "fixture_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}
