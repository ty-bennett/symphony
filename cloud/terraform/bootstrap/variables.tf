variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "existing_organization_id" {
  description = "Set to adopt an Organization that already exists in the management account (o-...); null creates a new one."
  type        = string
  default     = null

  validation {
    condition     = var.existing_organization_id == null || can(regex("^o-[a-z0-9]{10,32}$", var.existing_organization_id))
    error_message = "existing_organization_id must look like o-abc1234567."
  }
}

variable "security_ou_name" {
  description = "Name of the OU that holds the Control Tower service integration accounts."
  type        = string
  default     = "Security"
}

variable "log_archive_account_name" {
  description = "Display name of the log archive account."
  type        = string
  default     = "Log Archive"
}

variable "log_archive_account_email" {
  description = "Unique root email address for the log archive account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.log_archive_account_email))
    error_message = "log_archive_account_email must be an email address."
  }
}

variable "audit_account_name" {
  description = "Display name of the audit account."
  type        = string
  default     = "Audit"
}

variable "audit_account_email" {
  description = "Unique root email address for the audit account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.audit_account_email))
    error_message = "audit_account_email must be an email address."
  }

  validation {
    condition     = lower(var.audit_account_email) != lower(var.log_archive_account_email)
    error_message = "The audit and log archive accounts need different email addresses."
  }
}
