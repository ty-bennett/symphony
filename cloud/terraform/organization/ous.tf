locals {
  workload_ous = {
    infrastructure = "Infrastructure"
    customers      = "Customers"
    sandbox        = "Sandbox"
  }
}

resource "aws_organizations_organizational_unit" "workload" {
  for_each = local.workload_ous

  name      = each.value
  parent_id = local.bootstrap.organization_root_id

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

# Registers each OU with Control Tower so Account Factory can vend into it and
# Control Tower controls apply. Control Tower owns the resources this deploys.
resource "aws_controltower_baseline" "workload" {
  for_each = aws_organizations_organizational_unit.workload

  baseline_identifier = var.controltower_baseline_arn
  baseline_version    = var.controltower_baseline_version
  target_identifier   = each.value.arn

  parameters {
    key   = "IdentityCenterEnabledBaselineArn"
    value = var.identity_center_enabled_baseline_arn
  }

  timeouts {
    create = "60m"
    update = "60m"
    delete = "60m"
  }
}
