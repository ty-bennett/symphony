locals {
  name = "symphony-security-findings"
  rule_names = {
    guardduty   = "symphony-guardduty-findings"
    securityhub = "symphony-securityhub-findings"
  }
  event_patterns = {
    guardduty = {
      source        = ["aws.guardduty"]
      "detail-type" = ["GuardDuty Finding"]
      detail        = { severity = [{ numeric = [">=", var.guardduty_min_severity] }] }
    }
    securityhub = {
      source        = ["aws.securityhub"]
      "detail-type" = ["Security Hub Findings - Imported"]
      detail = {
        findings = {
          Severity    = { Label = ["HIGH", "CRITICAL"] }
          Workflow    = { Status = ["NEW"] }
          RecordState = ["ACTIVE"]
        }
      }
    }
  }
  rule_arns = [for name in values(local.rule_names) : "arn:aws:events:${var.region}:${var.account_id}:rule/${name}"]
}

# EventBridge cannot publish to an SNS topic encrypted with the AWS managed key,
# so the topic gets its own customer managed key.
resource "aws_kms_key" "alerts" {
  region                  = var.region
  description             = "Encrypts Symphony security finding notifications"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableAccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowEventBridgePublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
      },
    ]
  })
}

resource "aws_sns_topic" "alerts" {
  region            = var.region
  name              = local.name
  kms_master_key_id = aws_kms_key.alerts.arn
}

resource "aws_sns_topic_policy" "alerts" {
  region = var.region
  arn    = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowFindingRulesToPublish"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = "arn:aws:sns:${var.region}:${var.account_id}:${local.name}"
      Condition = { ArnEquals = { "aws:SourceArn" = local.rule_arns } }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "findings" {
  for_each = local.rule_names

  region        = var.region
  name          = each.value
  description   = "Forward high-severity ${each.key} findings to security contacts"
  event_pattern = jsonencode(local.event_patterns[each.key])
}

resource "aws_cloudwatch_event_target" "findings" {
  for_each = local.rule_names

  region    = var.region
  rule      = aws_cloudwatch_event_rule.findings[each.key].name
  target_id = "sns"
  arn       = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.subscriber_emails)

  region    = var.region
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}
