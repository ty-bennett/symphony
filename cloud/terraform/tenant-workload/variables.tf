variable "tenant_id" {
  description = "Tenant ID, matching a key of the organization root's tenants map."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.tenant_id))
    error_message = "tenant_id must be 3-31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "tenant_account_id" {
  description = "Tenant workload account ID (organization root output tenant_accounts)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.tenant_account_id))
    error_message = "tenant_account_id must be 12 digits."
  }
}

variable "workload_region" {
  description = "Primary Region for this tenant's workload resources."
  type        = string

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.workload_region)
    error_message = "workload_region must be us-east-1 or us-east-2."
  }
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap root."
  type        = string
}

variable "model_id" {
  description = "In-Region, on-demand Bedrock model for the private-cloud agent."
  type        = string
  default     = "moonshotai.kimi-k2.5"

  validation {
    condition     = contains(["moonshotai.kimi-k2.5", "zai.glm-5", "qwen.qwen3-32b-v1:0"], var.model_id)
    error_message = "model_id must be one of the benchmarked candidates: moonshotai.kimi-k2.5, zai.glm-5, qwen.qwen3-32b-v1:0."
  }
}

variable "step_up_model_id" {
  description = "US cross-Region inference profile used for hard tasks, chosen per request with the InvokeHarness model override."
  type        = string
  default     = "us.anthropic.claude-opus-4-5-20251101-v1:0"

  validation {
    condition     = contains(["us.anthropic.claude-opus-4-5-20251101-v1:0", "us.anthropic.claude-opus-5-5", "us.openai.gpt-6.1-sol"], var.step_up_model_id)
    error_message = "step_up_model_id must be a US cross-Region profile: us.anthropic.claude-opus-4-5-20251101-v1:0, us.anthropic.claude-opus-5-5, or us.openai.gpt-6.1-sol."
  }
}

variable "browser_allowed_domains" {
  description = "Hostnames the AgentCore Browser may open; every other URL is blocked by Chrome enterprise policy."
  type        = list(string)
  default     = ["docs.aws.amazon.com", "aws.amazon.com"]

  validation {
    condition     = length(var.browser_allowed_domains) >= 1 && alltrue([for d in var.browser_allowed_domains : can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", d))])
    error_message = "browser_allowed_domains must be a non-empty list of bare hostnames such as docs.aws.amazon.com."
  }
}

variable "trace_indexing_percent" {
  description = "Share of spans indexed by CloudWatch Transaction Search (raise to 100 only for a demo window)."
  type        = number
  default     = 1

  validation {
    condition     = var.trace_indexing_percent >= 1 && var.trace_indexing_percent <= 100
    error_message = "trace_indexing_percent must be between 1 and 100."
  }
}
