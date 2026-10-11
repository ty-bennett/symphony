data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "symphony/bootstrap.tfstate"
    region = "us-east-1"
  }
}

locals {
  bootstrap = data.terraform_remote_state.bootstrap.outputs
}
