variable "name" {
  description = "Globally unique S3 bucket name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.name))
    error_message = "Bucket name must be 3-63 lowercase letters, digits, dots, or hyphens."
  }
}

variable "kms_key_arn" {
  description = "Customer managed KMS key ARN used for default bucket encryption."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a KMS key ARN."
  }
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep noncurrent object versions."
  type        = number
  default     = 90

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be at least 1."
  }
}

variable "additional_policy_statements" {
  description = "Extra bucket policy statements appended after the TLS-only deny."
  type        = any
  default     = []
}
