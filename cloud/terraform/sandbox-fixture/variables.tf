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

variable "knowledge_base_reader_role_arns" {
  description = "Tenant knowledge base roles allowed to ingest approved-docs/ (tenant-workload output knowledge_base_role_arn)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for arn in var.knowledge_base_reader_role_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$", arn))])
    error_message = "knowledge_base_reader_role_arns must be IAM role ARNs."
  }
}
