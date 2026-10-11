mock_provider "aws" {}

variables {
  account_id        = "333333333333"
  region            = "us-east-2"
  subscriber_emails = ["security@example.com"]
}

run "forwards_high_guardduty_and_securityhub_findings" {
  command = plan

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.findings["guardduty"].event_pattern).detail.severity[0].numeric == [">=", 7]
    error_message = "GuardDuty findings of severity 7 and above must be forwarded."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.findings["securityhub"].event_pattern).detail.findings.Severity.Label == ["HIGH", "CRITICAL"]
    error_message = "Security Hub HIGH and CRITICAL findings must be forwarded."
  }

  assert {
    condition     = alltrue([for r in aws_cloudwatch_event_rule.findings : r.region == "us-east-2"])
    error_message = "Rules must be created in the module Region."
  }
}

run "only_finding_rules_may_publish" {
  command = plan

  assert {
    condition = toset(jsondecode(aws_sns_topic_policy.alerts.policy).Statement[0].Condition.ArnEquals["aws:SourceArn"]) == toset([
      "arn:aws:events:us-east-2:333333333333:rule/symphony-guardduty-findings",
      "arn:aws:events:us-east-2:333333333333:rule/symphony-securityhub-findings",
    ])
    error_message = "Only the two finding rules may publish to the topic."
  }
}

run "encrypts_the_topic_with_a_customer_key" {
  command = plan

  assert {
    condition     = aws_kms_key.alerts.enable_key_rotation
    error_message = "The alert key must rotate."
  }

  assert {
    condition     = jsondecode(aws_kms_key.alerts.policy).Statement[1].Principal.Service == "events.amazonaws.com"
    error_message = "EventBridge must be able to use the topic key."
  }
}

run "subscribes_every_contact" {
  command = plan

  assert {
    condition     = toset(keys(aws_sns_topic_subscription.email)) == toset(["security@example.com"])
    error_message = "Each security contact needs an email subscription."
  }
}

run "rejects_an_empty_contact_list" {
  command = plan

  variables {
    subscriber_emails = []
  }

  expect_failures = [var.subscriber_emails]
}

run "formats_guardduty_alerts_as_readable_text" {
  command = plan

  assert {
    condition     = one(aws_cloudwatch_event_target.findings["guardduty"].input_transformer).input_paths["title"] == "$.detail.title"
    error_message = "GuardDuty alerts must pull the finding title."
  }

  assert {
    condition     = startswith(one(aws_cloudwatch_event_target.findings["guardduty"].input_transformer).input_template, "\"[Symphony] GuardDuty finding, severity <severity>: <title>")
    error_message = "GuardDuty alerts must be a readable text message, not raw JSON."
  }
}

run "formats_security_hub_alerts_as_readable_text" {
  command = plan

  assert {
    condition     = one(aws_cloudwatch_event_target.findings["securityhub"].input_transformer).input_paths["title"] == "$.detail.findings[0].Title"
    error_message = "Security Hub alerts must pull the finding title."
  }

  assert {
    condition     = startswith(one(aws_cloudwatch_event_target.findings["securityhub"].input_transformer).input_template, "\"[Symphony] Security Hub finding, <severity>: <title>")
    error_message = "Security Hub alerts must be a readable text message, not raw JSON."
  }
}

run "does_not_email_guardduty_findings_twice" {
  command = plan

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.findings["securityhub"].event_pattern).detail.findings.ProductName == [{ "anything-but" = "GuardDuty" }]
    error_message = "Security Hub re-imports GuardDuty findings; its rule must skip them so each finding emails once."
  }
}
