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
