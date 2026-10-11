output "workload_ou_ids" {
  description = "IDs of the Infrastructure, Customers, and Sandbox OUs."
  value       = { for key, ou in aws_organizations_organizational_unit.workload : key => ou.id }
}

output "shared_services_account_id" {
  description = "Shared services account ID."
  value       = module.shared_services_account.account_id
}

output "fixture_account_id" {
  description = "Integration fixture account ID."
  value       = module.fixture_account.account_id
}

output "tenant_accounts" {
  description = "Tenant account ID and workload Region keyed by tenant ID."
  value = {
    for id, account in module.tenant_account : id => {
      account_id      = account.account_id
      workload_region = var.tenants[id].workload_region
    }
  }
}
