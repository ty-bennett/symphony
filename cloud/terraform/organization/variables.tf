variable "management_account_id" {
  description = "AWS account ID of the Symphony management account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "management_account_id must be 12 digits."
  }
}

variable "management_account_email" {
  description = "Root email of the management account, recorded on its GuardDuty membership."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.management_account_email))
    error_message = "management_account_email must be an email address."
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
    condition     = alltrue([for t in var.tenants : t.monthly_budget_usd > 0])
    error_message = "Each tenant needs a positive monthly_budget_usd."
  }

  validation {
    condition     = alltrue([for t in var.tenants : length(t.billing_contacts) >= 1 && length(distinct(concat(t.billing_contacts, var.operations_contacts))) <= 10])
    error_message = "Each tenant needs at least one billing contact, and at most 10 budget recipients including operations contacts."
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

variable "identity_center_instance_arn" {
  description = "Identity Center instance ARN (aws sso-admin list-instances)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:sso:::instance/ssoins-[a-f0-9]{16}$", var.identity_center_instance_arn))
    error_message = "identity_center_instance_arn must be an Identity Center instance ARN."
  }
}

variable "identity_store_id" {
  description = "Identity store ID of the Identity Center instance (aws sso-admin list-instances)."
  type        = string

  validation {
    condition     = can(regex("^d-[a-z0-9]{10}$", var.identity_store_id))
    error_message = "identity_store_id must look like d-1234567890."
  }
}

variable "identity_users" {
  description = "Identity Center users keyed by a stable handle. Groups: symphony-operators, symphony-security, tenant-<id>-admins, tenant-<id>-readonly."
  type = map(object({
    given_name  = string
    family_name = string
    email       = string
    groups      = list(string)
  }))
  default = {}

  validation {
    condition = alltrue(flatten([
      for user in var.identity_users : [
        for group in user.groups : contains(concat(
          ["symphony-operators", "symphony-security"],
          flatten([for id in keys(var.tenants) : ["tenant-${id}-admins", "tenant-${id}-readonly"]]),
        ), group)
      ]
    ]))
    error_message = "Every identity user group must be symphony-operators, symphony-security, or tenant-<id>-admins/readonly for a configured tenant."
  }
}

variable "operations_contacts" {
  description = "Symphony operations email addresses copied on every tenant budget alert."
  type        = list(string)

  validation {
    condition     = length(var.operations_contacts) >= 1
    error_message = "At least one operations contact is required."
  }
}

variable "budget_delivery_test_amount_usd" {
  description = "Set to a tiny amount (for example 1) to create a fixture-account budget that proves alert delivery; null removes it."
  type        = number
  default     = null
}
