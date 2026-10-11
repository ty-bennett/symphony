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
          # GuardDuty findings are also imported into Security Hub; the
          # GuardDuty rule already alerts on them.
          ProductName = [{ "anything-but" = "GuardDuty" }]
        }
      }
    }
  }
  rule_arns = [for name in values(local.rule_names) : "arn:aws:events:${var.region}:${var.account_id}:rule/${name}"]

  # Turn the raw event into a short plain-text email. A quoted template makes
  # EventBridge deliver a string (with values escaped) instead of JSON.
  alert_formats = {
    guardduty = {
      input_paths = {
        severity    = "$.detail.severity"
        title       = "$.detail.title"
        type        = "$.detail.type"
        account     = "$.detail.accountId"
        region      = "$.region"
        time        = "$.detail.updatedAt"
        description = "$.detail.description"
        id          = "$.detail.id"
      }
      input_template = join("\\n", [
        "\"[Symphony] GuardDuty finding, severity <severity>: <title>",
        "Account: <account>   Region: <region>",
        "Type: <type>",
        "When: <time>",
        "",
        "<description>",
        "",
        "Open in console: https://<region>.console.aws.amazon.com/guardduty/home?region=<region>#/findings?macros=current&fId=<id>\"",
      ])
    }
    securityhub = {
      input_paths = {
        severity    = "$.detail.findings[0].Severity.Label"
        title       = "$.detail.findings[0].Title"
        product     = "$.detail.findings[0].ProductName"
        account     = "$.detail.findings[0].AwsAccountId"
        region      = "$.detail.findings[0].Region"
        time        = "$.detail.findings[0].UpdatedAt"
        description = "$.detail.findings[0].Description"
      }
      input_template = join("\\n", [
        "\"[Symphony] Security Hub finding, <severity>: <title>",
        "Account: <account>   Region: <region>",
        "Source: <product>",
        "When: <time>",
        "",
        "<description>",
        "",
        "Open in console: https://<region>.console.aws.amazon.com/securityhub/home?region=<region>#/findings\"",
      ])
    }
  }
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

  input_transformer {
    input_paths    = local.alert_formats[each.key].input_paths
    input_template = local.alert_formats[each.key].input_template
  }
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.subscriber_emails)

  region    = var.region
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}
