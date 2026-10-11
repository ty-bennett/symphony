# Agent traces (CloudWatch Transaction Search). Harness spans include prompt and
# tool payloads, so they stay in this tenant account only: tenant-key encryption
# and 7-day retention. Nothing here is exported to Symphony's accounts.
resource "aws_cloudwatch_log_group" "spans" {
  name              = "aws/spans"
  retention_in_days = 7
  kms_key_id        = local.tenant_baseline.tenant_data_kms_key_arn

  depends_on = [terraform_data.account_guard]
}

resource "aws_cloudwatch_log_resource_policy" "xray_spans" {
  policy_name = "symphony-transaction-search"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TransactionSearchXRayAccess"
      Effect    = "Allow"
      Principal = { Service = "xray.amazonaws.com" }
      Action    = "logs:PutLogEvents"
      Resource = [
        "arn:aws:logs:${var.workload_region}:${var.tenant_account_id}:log-group:aws/spans:*",
        "arn:aws:logs:${var.workload_region}:${var.tenant_account_id}:log-group:/aws/application-signals/data:*",
      ]
      Condition = {
        ArnLike      = { "aws:SourceArn" = "arn:aws:xray:${var.workload_region}:${var.tenant_account_id}:*" }
        StringEquals = { "aws:SourceAccount" = var.tenant_account_id }
      }
    }]
  })
}

resource "aws_xray_trace_segment_destination" "this" {
  destination = "CloudWatchLogs"

  depends_on = [aws_cloudwatch_log_group.spans, aws_cloudwatch_log_resource_policy.xray_spans]
}

resource "aws_xray_indexing_rule" "default" {
  name = "Default"

  rule {
    probabilistic {
      desired_sampling_percentage = var.trace_indexing_percent
    }
  }

  depends_on = [aws_xray_trace_segment_destination.this]
}
