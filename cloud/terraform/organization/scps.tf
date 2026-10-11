locals {
  controltower_execution_role = "arn:aws:iam::*:role/AWSControlTowerExecution"

  # Global services exempt from the Region deny. Based on the AWS Organizations
  # example SCP "Deny access to AWS based on the requested AWS Region", plus
  # Identity Center and account settings used by Control Tower.
  global_service_actions = [
    "a4b:*", "account:*", "acm:*", "aws-marketplace-management:*", "aws-marketplace:*",
    "aws-portal:*", "budgets:*", "ce:*", "chime:*", "cloudfront:*", "config:*", "cur:*",
    "directconnect:*", "ec2:DescribeRegions", "ec2:DescribeTransitGateways",
    "ec2:DescribeVpnGateways", "fms:*", "globalaccelerator:*", "health:*", "iam:*",
    "identitystore:*", "importexport:*", "kms:*", "mobileanalytics:*", "networkmanager:*",
    "organizations:*", "pricing:*", "route53-recovery-cluster:*",
    "route53-recovery-control-config:*", "route53-recovery-readiness:*", "route53:*",
    "route53domains:*", "s3:GetAccountPublic*", "s3:ListAllMyBuckets",
    "s3:ListMultiRegionAccessPoints", "s3:PutAccountPublic*", "shield:*", "sso:*", "sts:*",
    "support:*", "trustedadvisor:*", "waf-regional:*", "waf:*", "wafv2:*", "wellarchitected:*",
  ]

  # US-only cross-Region inference profiles (us.*) also route to us-west-2.
  bedrock_inference_actions = [
    "bedrock:Converse",
    "bedrock:ConverseStream",
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
  ]
  bedrock_inference_regions = concat(local.allowed_regions, ["us-west-2"])

  service_control_policies = {
    region-guardrail = {
      description = "Deny regional services outside us-east-1 and us-east-2 (Bedrock inference also us-west-2)."
      ous         = keys(local.workload_ous)
      document = {
        Version = "2012-10-17"
        Statement = [
          {
            Sid       = "DenyOutsideApprovedRegions"
            Effect    = "Deny"
            NotAction = concat(local.global_service_actions, local.bedrock_inference_actions)
            Resource  = "*"
            Condition = {
              StringNotEquals = { "aws:RequestedRegion" = local.allowed_regions }
              ArnNotLike      = { "aws:PrincipalARN" = [local.controltower_execution_role] }
            }
          },
          {
            Sid      = "DenyBedrockInferenceOutsideUS"
            Effect   = "Deny"
            Action   = local.bedrock_inference_actions
            Resource = "*"
            Condition = {
              StringNotEquals = { "aws:RequestedRegion" = local.bedrock_inference_regions }
              ArnNotLike      = { "aws:PrincipalARN" = [local.controltower_execution_role] }
            }
          },
        ]
      }
    }

    baseline-protection = {
      description = "Protect organization membership, security services, and keys."
      ous         = keys(local.workload_ous)
      document = {
        Version = "2012-10-17"
        Statement = [
          {
            Sid      = "DenyLeavingOrganization"
            Effect   = "Deny"
            Action   = "organizations:LeaveOrganization"
            Resource = "*"
          },
          {
            Sid       = "DenyRootUser"
            Effect    = "Deny"
            Action    = "*"
            Resource  = "*"
            Condition = { StringLike = { "aws:PrincipalArn" = "arn:aws:iam::*:root" } }
          },
          {
            Sid       = "DenyLongLivedIamUserCredentials"
            Effect    = "Deny"
            Action    = ["iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile"]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
          {
            Sid    = "DenySecurityBaselineChanges"
            Effect = "Deny"
            Action = [
              "access-analyzer:DeleteAnalyzer",
              "ec2:DisableEbsEncryptionByDefault",
              "guardduty:DeleteDetector",
              "guardduty:DisassociateFromAdministratorAccount",
              "guardduty:DisassociateFromMasterAccount",
              "s3:DeleteAccountPublicAccessBlock",
              "s3:PutAccountPublicAccessBlock",
              "securityhub:DisableSecurityHub",
              "securityhub:DisassociateFromAdministratorAccount",
              "securityhub:DisassociateFromMasterAccount",
            ]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
          {
            Sid       = "DenyKmsKeyDeletion"
            Effect    = "Deny"
            Action    = ["kms:ScheduleKeyDeletion", "kms:DisableKey"]
            Resource  = "*"
            Condition = { ArnNotLike = { "aws:PrincipalARN" = [local.controltower_execution_role] } }
          },
        ]
      }
    }

    # Customer accounts must keep Bedrock data retention at "none" (zero data
    # retention). No principal is exempt, so retention can only ever be set to none.
    zero-data-retention = {
      description = "Customer accounts may only set Bedrock data retention to none."
      ous         = ["customers"]
      document = {
        Version = "2012-10-17"
        Statement = [
          {
            Sid       = "DenyBedrockRetentionOtherThanNone"
            Effect    = "Deny"
            Action    = "bedrock:PutAccountDataRetention"
            Resource  = "*"
            Condition = { StringNotEquals = { "bedrock:DataRetentionMode" = "none" } }
          },
          {
            Sid       = "DenyMantleRetentionOtherThanNone"
            Effect    = "Deny"
            Action    = ["bedrock-mantle:PutAccountDataRetention", "bedrock-mantle:CreateProject", "bedrock-mantle:UpdateProject"]
            Resource  = "*"
            Condition = { StringNotEquals = { "bedrock-mantle:DataRetentionMode" = "none" } }
          },
        ]
      }
    }
  }

  scp_attachments = merge([
    for name, policy in local.service_control_policies : {
      for ou in policy.ous : "${name}/${ou}" => { policy = name, ou = ou }
    }
  ]...)
}

resource "aws_organizations_policy" "scp" {
  for_each = local.service_control_policies

  name        = "symphony-${each.key}"
  description = each.value.description
  type        = "SERVICE_CONTROL_POLICY"
  content     = jsonencode(each.value.document)

  depends_on = [terraform_data.account_guard]
}

resource "aws_organizations_policy_attachment" "scp" {
  for_each = local.scp_attachments

  policy_id = aws_organizations_policy.scp[each.value.policy].id
  target_id = aws_organizations_organizational_unit.workload[each.value.ou].id
}
