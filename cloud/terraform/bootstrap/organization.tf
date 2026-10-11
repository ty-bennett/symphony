resource "aws_organizations_organization" "this" {
  feature_set          = "ALL"
  enabled_policy_types = ["SERVICE_CONTROL_POLICY"]

  lifecycle {
    prevent_destroy = true
    # Control Tower and the organization root enable trusted access and policy types
    # after creation. Terraform sets only the initial values and never removes theirs.
    ignore_changes = [aws_service_access_principals, enabled_policy_types]
  }
}

# Landing zone 4.0 no longer creates the Security OU; the service integration
# accounts must share one OU directly under the root before CreateLandingZone.
resource "aws_organizations_organizational_unit" "security" {
  name      = var.security_ou_name
  parent_id = aws_organizations_organization.this.roots[0].id

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_organizations_account" "log_archive" {
  name                       = var.log_archive_account_name
  email                      = var.log_archive_account_email
  parent_id                  = aws_organizations_organizational_unit.security.id
  close_on_deletion          = false
  iam_user_access_to_billing = "DENY"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [role_name, iam_user_access_to_billing]
  }
}

resource "aws_organizations_account" "audit" {
  name                       = var.audit_account_name
  email                      = var.audit_account_email
  parent_id                  = aws_organizations_organizational_unit.security.id
  close_on_deletion          = false
  iam_user_access_to_billing = "DENY"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [role_name, iam_user_access_to_billing]
  }
}
