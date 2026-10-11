# Sandboxed Python/JS execution with no network: the agent passes data in (for
# example a CSV read through the documents tool); the sandbox cannot fetch more.
resource "aws_bedrockagentcore_code_interpreter" "sandbox" {
  name        = "symphony_${replace(var.tenant_id, "-", "_")}_sandbox"
  description = "Network-isolated code execution for the ${var.tenant_id} agent"

  network_configuration {
    network_mode = "SANDBOX"
  }

  depends_on = [terraform_data.account_guard]
}
