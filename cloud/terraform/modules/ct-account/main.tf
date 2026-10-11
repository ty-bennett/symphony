# Vends one account through Control Tower Account Factory. Removing this
# resource would terminate the provisioned product, so destruction is blocked.
resource "aws_servicecatalog_provisioned_product" "this" {
  name                       = var.provisioned_product_name
  product_name               = var.product_name
  provisioning_artifact_name = var.provisioning_artifact_name

  provisioning_parameters {
    key   = "AccountName"
    value = var.account_name
  }

  provisioning_parameters {
    key   = "AccountEmail"
    value = var.account_email
  }

  provisioning_parameters {
    key   = "ManagedOrganizationalUnit"
    value = "${var.organizational_unit.name} (${var.organizational_unit.id})"
  }

  provisioning_parameters {
    key   = "SSOUserEmail"
    value = var.sso_user.email
  }

  provisioning_parameters {
    key   = "SSOUserFirstName"
    value = var.sso_user.first_name
  }

  provisioning_parameters {
    key   = "SSOUserLastName"
    value = var.sso_user.last_name
  }

  timeouts {
    create = "60m"
    update = "60m"
    delete = "60m"
  }

  lifecycle {
    prevent_destroy = true
  }
}
