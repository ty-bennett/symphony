variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "landing_zone_version" {
  description = "Control Tower landing zone version. The manifest format below is for 4.x."
  type        = string
  default     = "4.0"

  validation {
    condition     = can(regex("^4\\.[0-9]+$", var.landing_zone_version))
    error_message = "This manifest uses the landing zone 4.x format; set a 4.x version."
  }
}

variable "log_retention_days" {
  description = "Retention for CloudTrail and Config logs in the log archive bucket."
  type        = number
  default     = 365

  validation {
    condition     = var.log_retention_days >= 1
    error_message = "log_retention_days must be at least 1."
  }
}

variable "access_log_retention_days" {
  description = "Retention for S3 access logs of the log archive bucket."
  type        = number
  default     = 365

  validation {
    condition     = var.access_log_retention_days >= 1
    error_message = "access_log_retention_days must be at least 1."
  }
}
