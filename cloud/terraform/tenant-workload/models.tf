# The harness may call exactly two models: the in-Region default and one US
# cross-Region step-up profile. A cross-Region call needs the profile ARN plus
# the foundation model in every Region the profile can route to.
locals {
  step_up_regions    = ["us-east-1", "us-east-2", "us-west-2"]
  step_up_base_model = trimprefix(var.step_up_model_id, "us.")
  allowed_model_arns = concat(
    ["arn:aws:bedrock:${var.workload_region}::foundation-model/${var.model_id}"],
    ["arn:aws:bedrock:${var.workload_region}:${var.tenant_account_id}:inference-profile/${var.step_up_model_id}"],
    [for region in local.step_up_regions : "arn:aws:bedrock:${region}::foundation-model/${local.step_up_base_model}"],
  )
}
