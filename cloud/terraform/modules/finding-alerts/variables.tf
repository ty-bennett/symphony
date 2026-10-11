variable "account_id" {
  description = "Account that owns the alert topic (the delegated security administrator)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "region" {
  description = "Region whose GuardDuty and Security Hub findings this module forwards."
  type        = string
}

variable "subscriber_emails" {
  description = "Security contacts notified of high-severity findings."
  type        = list(string)

  validation {
    condition     = length(var.subscriber_emails) >= 1
    error_message = "At least one security contact is required, or findings reach nobody."
  }
}

variable "guardduty_min_severity" {
  description = "Lowest GuardDuty severity forwarded (7 = high)."
  type        = number
  default     = 7
}
