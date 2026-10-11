output "workload_ou_ids" {
  description = "IDs of the Infrastructure, Customers, and Sandbox OUs."
  value       = { for key, ou in aws_organizations_organizational_unit.workload : key => ou.id }
}
