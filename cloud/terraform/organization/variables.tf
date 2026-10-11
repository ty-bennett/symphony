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

variable "account_factory_portfolio_id" {
  description = "ID of the 'AWS Control Tower Account Factory Portfolio' (aws servicecatalog list-portfolios)."
  type        = string

  validation {
    condition     = can(regex("^port-[a-z0-9]+$", var.account_factory_portfolio_id))
    error_message = "account_factory_portfolio_id must look like port-abc123."
  }
}

variable "account_factory_principal" {
  description = "IAM principal running Terraform, granted access to the Account Factory portfolio."
  type = object({
    arn  = string
    type = optional(string, "IAM")
  })

  validation {
    condition     = contains(["IAM", "IAM_PATTERN"], var.account_factory_principal.type)
    error_message = "account_factory_principal.type must be IAM or IAM_PATTERN."
  }
}

variable "account_factory_sso_user" {
  description = "Identity Center user Account Factory assigns to each vended account (Symphony operations)."
  type = object({
    email      = string
    first_name = string
    last_name  = string
  })
}

variable "shared_services_account" {
  description = "Shared services account in the Infrastructure OU."
  type = object({
    name  = optional(string, "Shared Services")
    email = string
  })
}

variable "fixture_account" {
  description = "Low-cost integration fixture account in the Sandbox OU."
  type = object({
    name  = optional(string, "Integration Fixture")
    email = string
  })
}

variable "tenants" {
  description = "Customer workload accounts keyed by tenant ID."
  type = map(object({
    display_name       = string
    account_email      = string
    workload_region    = string
    monthly_budget_usd = number
    billing_contacts   = list(string)
  }))

  validation {
    condition     = length(var.tenants) >= 1 && alltrue([for id in keys(var.tenants) : can(regex("^[a-z][a-z0-9-]{2,30}$", id))])
    error_message = "Define at least one tenant; tenant IDs are 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }

  validation {
    condition     = alltrue([for t in var.tenants : contains(["us-east-1", "us-east-2"], t.workload_region)])
    error_message = "Tenant workload_region must be us-east-1 or us-east-2."
  }

  validation {
    condition = length(distinct([
      for email in concat(
        [var.shared_services_account.email, var.fixture_account.email],
        [for t in var.tenants : t.account_email],
      ) : lower(email)
    ])) == 2 + length(var.tenants)
    error_message = "Every vended account needs its own email address."
  }
}

variable "security_contacts" {
  description = "Email addresses notified of high-severity GuardDuty and Security Hub findings."
  type        = list(string)

  validation {
    condition     = length(var.security_contacts) >= 1
    error_message = "At least one security contact is required."
  }
}
