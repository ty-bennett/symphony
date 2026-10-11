variable "account_name" {
  description = "Display name of the vended account."
  type        = string
}

variable "account_email" {
  description = "Unique root email address for the vended account."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.account_email))
    error_message = "account_email must be an email address."
  }
}

variable "provisioned_product_name" {
  description = "Service Catalog provisioned product name; letters, digits, dot, underscore, hyphen."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$", var.provisioned_product_name))
    error_message = "provisioned_product_name may contain only letters, digits, '.', '_', and '-'."
  }
}

variable "organizational_unit" {
  description = "Registered OU that receives the account."
  type = object({
    id   = string
    name = string
  })
}

variable "sso_user" {
  description = "Identity Center user that Account Factory grants access to the new account."
  type = object({
    email      = string
    first_name = string
    last_name  = string
  })
}

variable "product_name" {
  description = "Account Factory product name in Service Catalog."
  type        = string
  default     = "AWS Control Tower Account Factory"
}

variable "provisioning_artifact_name" {
  description = "Account Factory provisioning artifact name."
  type        = string
  default     = "AWS Control Tower Account Factory"
}
