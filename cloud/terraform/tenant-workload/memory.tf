# Per-user memory: short-term chat events plus long-term facts and summaries,
# encrypted with the tenant key and expired after 30 days. InvokeHarness passes
# actorId per request, so each user's memory is isolated.
locals {
  memory_name = "symphony_${replace(var.tenant_id, "-", "_")}_memory"
}

resource "aws_iam_role" "memory" {
  name = "${local.name_prefix}-memory"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.tenant_account_id } }
    }]
  })

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_role_policy_attachment" "memory" {
  role       = aws_iam_role.memory.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy"
}

resource "aws_bedrockagentcore_memory" "agent" {
  name                      = local.memory_name
  description               = "Per-user memory for the ${var.tenant_id} agent"
  encryption_key_arn        = local.tenant_baseline.tenant_data_kms_key_arn
  event_expiry_duration     = 30
  memory_execution_role_arn = aws_iam_role.memory.arn

  depends_on = [aws_iam_role_policy_attachment.memory]
}

resource "aws_bedrockagentcore_memory_strategy" "facts" {
  memory_id           = aws_bedrockagentcore_memory.agent.id
  name                = "facts"
  type                = "SEMANTIC"
  namespace_templates = ["/users/{actorId}/facts"]
}

resource "aws_bedrockagentcore_memory_strategy" "summaries" {
  memory_id           = aws_bedrockagentcore_memory.agent.id
  name                = "summaries"
  type                = "SUMMARIZATION"
  namespace_templates = ["/users/{actorId}/sessions/{sessionId}"]
}
