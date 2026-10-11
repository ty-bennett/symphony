# First apply only: create backend_override.tf (gitignored) containing
#   terraform { backend "local" {} }
# then migrate to S3 as described in README.md.
terraform {
  backend "s3" {}
}
