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

output "step_up_model_id" {
  description = "Cross-Region step-up model to pass as --model-id."
  value       = var.step_up_model_id
}

output "knowledge_base_id" {
  description = "Knowledge base to sync with tenant_ops.py kb-sync."
  value       = aws_bedrockagent_knowledge_base.documents.id
}

output "knowledge_base_data_source_id" {
  description = "Data source to sync with tenant_ops.py kb-sync."
  value       = aws_bedrockagent_data_source.approved_docs.data_source_id
}

output "knowledge_base_role_arn" {
  description = "Role the document-owning account must allow to read approved-docs/ (sandbox-fixture knowledge_base_reader_role_arns)."
  value       = aws_iam_role.knowledge_base.arn
}

output "browser_recordings_bucket" {
  description = "Encrypted bucket holding browser session recordings."
  value       = module.browser_bucket.name
}

output "memory_id" {
  description = "AgentCore Memory resource for per-user recall."
  value       = aws_bedrockagentcore_memory.agent.id
}
