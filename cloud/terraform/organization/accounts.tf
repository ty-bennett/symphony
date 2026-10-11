# Lets the Terraform principal launch the Account Factory product.
resource "aws_servicecatalog_principal_portfolio_association" "terraform" {
  portfolio_id   = var.account_factory_portfolio_id
  principal_arn  = var.account_factory_principal.arn
  principal_type = var.account_factory_principal.type

  depends_on = [terraform_data.account_guard]
}

module "shared_services_account" {
  source = "../modules/ct-account"

  account_name             = var.shared_services_account.name
  account_email            = var.shared_services_account.email
  provisioned_product_name = "symphony-shared-services"
  organizational_unit      = aws_organizations_organizational_unit.workload["infrastructure"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}

module "fixture_account" {
  source = "../modules/ct-account"

  account_name             = var.fixture_account.name
  account_email            = var.fixture_account.email
  provisioned_product_name = "symphony-integration-fixture"
  organizational_unit      = aws_organizations_organizational_unit.workload["sandbox"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}

module "tenant_account" {
  source   = "../modules/ct-account"
  for_each = var.tenants

  account_name             = "Customer ${each.value.display_name}"
  account_email            = each.value.account_email
  provisioned_product_name = "symphony-tenant-${each.key}"
  organizational_unit      = aws_organizations_organizational_unit.workload["customers"]
  sso_user                 = var.account_factory_sso_user

  depends_on = [aws_controltower_baseline.workload, aws_servicecatalog_principal_portfolio_association.terraform]
}
