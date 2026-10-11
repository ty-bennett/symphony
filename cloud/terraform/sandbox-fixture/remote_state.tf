data "terraform_remote_state" "organization" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/organization.tfstate"
    region = "us-east-1"
  }
}

locals {
  organization = data.terraform_remote_state.organization.outputs
}
