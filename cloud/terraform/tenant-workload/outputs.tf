output "harness_arn" {
  description = "AgentCore Harness ARN to pass to InvokeHarness."
  value       = aws_bedrockagentcore_harness.agent.arn
}

output "gateway_id" {
  description = "Tenant AgentCore Gateway ID."
  value       = aws_bedrockagentcore_gateway.documents.gateway_id
}

output "documents_tool_role_arn" {
  description = "Role the customer-side SymphonyIntegrationRole must trust."
  value       = aws_iam_role.documents_tool.arn
}

output "external_id" {
  description = "External ID the customer-side role requires (an extra trust condition, not a secret)."
  value       = random_uuid.external_id.result
}

output "model_id" {
  description = "Bedrock model the harness uses."
  value       = var.model_id
}
