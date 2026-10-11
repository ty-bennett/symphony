# AWS Budgets notify; they are not a spending cap. Defaults from PLAN.md:
# 80% forecast, 80% actual, and 100% actual of the customer's monthly amount.
locals {
  budget_notifications = [
    { notification_type = "FORECASTED", threshold = 80 },
    { notification_type = "ACTUAL", threshold = 80 },
    { notification_type = "ACTUAL", threshold = 100 },
  ]
}

resource "aws_budgets_budget" "tenant" {
  for_each = var.tenants

  name         = "symphony-tenant-${each.key}-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", each.value.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "LinkedAccount"
    values = [module.tenant_account[each.key].account_id]
  }

  dynamic "notification" {
    for_each = local.budget_notifications

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = distinct(concat(each.value.billing_contacts, var.operations_contacts))
    }
  }

  depends_on = [terraform_data.account_guard]
}

# Temporary low-threshold budget on the fixture account to prove that
# notifications reach operations. Remove it by setting the amount to null.
resource "aws_budgets_budget" "delivery_test" {
  count = var.budget_delivery_test_amount_usd == null ? 0 : 1

  name         = "symphony-budget-delivery-test"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_delivery_test_amount_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "LinkedAccount"
    values = [module.fixture_account.account_id]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.operations_contacts
  }
}
