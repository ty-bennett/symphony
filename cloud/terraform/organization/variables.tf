variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "audit_account_id" {
  description = "AWS account ID of the audit account (from bootstrap outputs)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.audit_account_id))
    error_message = "audit_account_id must be 12 digits."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "controltower_baseline_arn" {
  description = "ARN of the AWSControlTowerBaseline baseline (aws controltower list-baselines)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:controltower:us-east-1::baseline/[A-Z0-9]+$", var.controltower_baseline_arn))
    error_message = "controltower_baseline_arn must be a us-east-1 Control Tower baseline ARN."
  }
}

variable "controltower_baseline_version" {
  description = "AWSControlTowerBaseline version compatible with the deployed landing zone."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+$", var.controltower_baseline_version))
    error_message = "controltower_baseline_version must look like 5.0."
  }
}

variable "identity_center_enabled_baseline_arn" {
  description = "ARN of the enabled IdentityCenterBaseline (aws controltower list-enabled-baselines)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:controltower:us-east-1:[0-9]{12}:enabledbaseline/[A-Z0-9]+$", var.identity_center_enabled_baseline_arn))
    error_message = "identity_center_enabled_baseline_arn must be an enabled baseline ARN."
  }
}
