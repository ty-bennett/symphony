# Shared placeholder inputs for every organization test file.
management_account_id                = "111111111111"
audit_account_id                     = "333333333333"
state_bucket                         = "symphony-tfstate-111111111111"
controltower_baseline_arn            = "arn:aws:controltower:us-east-1::baseline/17BSJV3IGJ2QSGA2"
controltower_baseline_version        = "5.0"
identity_center_enabled_baseline_arn = "arn:aws:controltower:us-east-1:111111111111:enabledbaseline/XALULM96QHI525UOC"

account_factory_portfolio_id = "port-abc123example"
account_factory_principal    = { arn = "arn:aws:iam::111111111111:role/SymphonyTerraform" }
account_factory_sso_user     = { email = "ops@example.com", first_name = "Symphony", last_name = "Operations" }
shared_services_account      = { email = "aws+shared@example.com" }
fixture_account              = { email = "aws+fixture@example.com" }
tenants = {
  acme = {
    display_name       = "Acme Pilot"
    account_email      = "aws+acme@example.com"
    workload_region    = "us-east-1"
    monthly_budget_usd = 250
    billing_contacts   = ["billing@acme.example.com"]
  }
}

security_contacts = ["security@example.com"]

identity_center_instance_arn = "arn:aws:sso:::instance/ssoins-1111111111111111"
identity_store_id            = "d-1111111111"

operations_contacts = ["ops@example.com"]
