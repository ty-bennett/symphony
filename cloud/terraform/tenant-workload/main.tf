# Phase 3 agent slice: an AgentCore Harness (managed Strands loop) on in-Region
# Bedrock, an IAM-authenticated AgentCore Gateway, and the read-only S3 document
# tool as its only target. The Regional API Gateway ingress (mTLS + Cognito) is
# a later plan; until then the harness is invoked with SigV4 by operators.
locals {
  name_prefix      = "symphony-${var.tenant_id}"
  harness_name     = "symphony_${replace(var.tenant_id, "-", "_")}_agent"
  tool_target_name = "customer-documents"
  fixture_account  = local.organization.fixture_account_id
  external_role    = "arn:aws:iam::${local.fixture_account}:role/SymphonyIntegrationRole"
}
