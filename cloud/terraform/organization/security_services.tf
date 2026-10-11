# GuardDuty and Security Hub CSPM are Regional: each governed Region gets a
# detector/hub in the audit account (the delegated administrator) and in the
# management account, then auto-enrollment for every member account.
locals {
  security_regions = toset(local.allowed_regions)
}

resource "aws_organizations_aws_service_access" "security" {
  for_each = toset(["guardduty.amazonaws.com", "securityhub.amazonaws.com"])

  service_principal = each.value

  depends_on = [terraform_data.account_guard]
}

resource "aws_guardduty_detector" "management" {
  for_each = local.security_regions

  region                       = each.value
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

resource "aws_guardduty_detector" "audit" {
  provider = aws.audit
  for_each = local.security_regions

  region                       = each.value
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

resource "aws_guardduty_organization_admin_account" "this" {
  for_each = local.security_regions

  region           = each.value
  admin_account_id = var.audit_account_id

  depends_on = [aws_organizations_aws_service_access.security, aws_guardduty_detector.audit]
}

resource "aws_guardduty_organization_configuration" "this" {
  provider = aws.audit
  for_each = local.security_regions

  region                           = each.value
  detector_id                      = aws_guardduty_detector.audit[each.key].id
  auto_enable_organization_members = "ALL"

  depends_on = [aws_guardduty_organization_admin_account.this]
}

resource "aws_securityhub_account" "management" {
  for_each = local.security_regions

  region                   = each.value
  enable_default_standards = false
}

resource "aws_securityhub_account" "audit" {
  provider = aws.audit
  for_each = local.security_regions

  region                   = each.value
  enable_default_standards = false
}

resource "aws_securityhub_organization_admin_account" "this" {
  for_each = local.security_regions

  region           = each.value
  admin_account_id = var.audit_account_id

  depends_on = [aws_organizations_aws_service_access.security, aws_securityhub_account.management, aws_securityhub_account.audit]
}

# Standards stay off: Control Tower deploys its own service-managed standard,
# and extra standards add per-check cost.
resource "aws_securityhub_organization_configuration" "this" {
  provider = aws.audit
  for_each = local.security_regions

  region                = each.value
  auto_enable           = true
  auto_enable_standards = "NONE"

  organization_configuration {
    configuration_type = "LOCAL"
  }

  depends_on = [aws_securityhub_organization_admin_account.this]
}

module "finding_alerts" {
  source   = "../modules/finding-alerts"
  for_each = local.security_regions

  providers = {
    aws = aws.audit
  }

  account_id        = var.audit_account_id
  region            = each.value
  subscriber_emails = var.security_contacts
}
