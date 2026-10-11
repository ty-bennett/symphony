variable "regions" {
  description = "Regions where this account runs workloads."
  type        = set(string)

  validation {
    condition     = length(var.regions) >= 1 && alltrue([for r in var.regions : contains(["us-east-1", "us-east-2"], r)])
    error_message = "regions must be a non-empty subset of us-east-1 and us-east-2."
  }
}
