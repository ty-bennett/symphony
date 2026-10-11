output "account_id" {
  description = "ID of the vended account."
  value       = one([for output in aws_servicecatalog_provisioned_product.this.outputs : output.value if output.key == "AccountId"])
}
