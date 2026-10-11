data "terraform_remote_state" "organization" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/organization.tfstate"
    region = "us-east-1"
  }
}

data "terraform_remote_state" "sandbox_fixture" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/sandbox-fixture.tfstate"
    region = "us-east-1"
  }
}

data "terraform_remote_state" "tenant_workload" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/tenants/${var.tenant_id}/workload.tfstate"
    region = "us-east-1"
  }
}

locals {
  organization = data.terraform_remote_state.organization.outputs
  fixture      = data.terraform_remote_state.sandbox_fixture.outputs
  tenant       = data.terraform_remote_state.tenant_workload.outputs
}
