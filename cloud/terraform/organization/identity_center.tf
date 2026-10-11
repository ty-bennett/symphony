# Symphony-owned permission sets, groups, and assignments. Control Tower's own
# AWSControlTower* groups and permission sets are left untouched.
# MFA enforcement is an Identity Center setting with no API; see the runbook.
locals {
  permission_sets = {
    SymphonyPlatformAdmin = {
      description      = "Symphony operators: full access to workload accounts."
      session_duration = "PT1H"
      managed_policies = ["arn:aws:iam::aws:policy/AdministratorAccess"]
    }
    SymphonySecurityAudit = {
      description      = "Symphony security: read security configuration, no data access."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/SecurityAudit", "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"]
    }
    CustomerAdmin = {
      description      = "Customer administrators: manage workload resources in their own account."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/PowerUserAccess"]
    }
    CustomerReadOnly = {
      description      = "Customer users: read-only access to their own account."
      session_duration = "PT4H"
      managed_policies = ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
    }
  }

  permission_set_policies = {
    for pair in flatten([
      for name, config in local.permission_sets : [
        for arn in config.managed_policies : { key = "${name}/${basename(arn)}", permission_set = name, arn = arn }
      ]
    ]) : pair.key => pair
  }

  identity_groups = merge(
    {
      symphony-operators = "Symphony platform operators"
      symphony-security  = "Symphony security reviewers"
    },
    merge([
      for id, tenant in var.tenants : {
        "tenant-${id}-admins"   = "${tenant.display_name} administrators"
        "tenant-${id}-readonly" = "${tenant.display_name} read-only users"
      }
    ]...),
  )

  workload_account_ids = merge(
    {
      shared-services = module.shared_services_account.account_id
      fixture         = module.fixture_account.account_id
    },
    { for id, account in module.tenant_account : "tenant-${id}" => account.account_id },
  )

  security_account_ids = merge(local.workload_account_ids, {
    audit       = var.audit_account_id
    log-archive = local.bootstrap.log_archive_account_id
  })

  account_assignments = merge(
    {
      for key, account_id in local.workload_account_ids : "symphony-operators/SymphonyPlatformAdmin/${key}" => {
        group = "symphony-operators", permission_set = "SymphonyPlatformAdmin", account_id = account_id
      }
    },
    {
      for key, account_id in local.security_account_ids : "symphony-security/SymphonySecurityAudit/${key}" => {
        group = "symphony-security", permission_set = "SymphonySecurityAudit", account_id = account_id
      }
    },
    merge([
      for id, account in module.tenant_account : {
        "tenant-${id}-admins/CustomerAdmin/tenant-${id}" = {
          group = "tenant-${id}-admins", permission_set = "CustomerAdmin", account_id = account.account_id
        }
        "tenant-${id}-readonly/CustomerReadOnly/tenant-${id}" = {
          group = "tenant-${id}-readonly", permission_set = "CustomerReadOnly", account_id = account.account_id
        }
      }
    ]...),
  )

  group_memberships = {
    for pair in flatten([
      for handle, user in var.identity_users : [
        for group in user.groups : { key = "${handle}/${group}", user = handle, group = group }
      ]
    ]) : pair.key => pair
  }
}

resource "aws_ssoadmin_permission_set" "this" {
  for_each = local.permission_sets

  name             = each.key
  description      = each.value.description
  instance_arn     = var.identity_center_instance_arn
  session_duration = each.value.session_duration

  depends_on = [terraform_data.account_guard]
}

resource "aws_ssoadmin_managed_policy_attachment" "this" {
  for_each = local.permission_set_policies

  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.permission_set].arn
  managed_policy_arn = each.value.arn
}

resource "aws_identitystore_group" "this" {
  for_each = local.identity_groups

  identity_store_id = var.identity_store_id
  display_name      = each.key
  description       = each.value
}

resource "aws_identitystore_user" "this" {
  for_each = var.identity_users

  identity_store_id = var.identity_store_id
  user_name         = each.value.email
  display_name      = "${each.value.given_name} ${each.value.family_name}"

  name {
    given_name  = each.value.given_name
    family_name = each.value.family_name
  }

  emails {
    value   = each.value.email
    primary = true
    type    = "work"
  }
}

resource "aws_identitystore_group_membership" "this" {
  for_each = local.group_memberships

  identity_store_id = var.identity_store_id
  group_id          = aws_identitystore_group.this[each.value.group].group_id
  member_id         = aws_identitystore_user.this[each.value.user].user_id
}

resource "aws_ssoadmin_account_assignment" "this" {
  for_each = local.account_assignments

  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.permission_set].arn
  principal_type     = "GROUP"
  principal_id       = aws_identitystore_group.this[each.value.group].group_id
  target_type        = "AWS_ACCOUNT"
  target_id          = each.value.account_id

  depends_on = [aws_ssoadmin_managed_policy_attachment.this]
}
