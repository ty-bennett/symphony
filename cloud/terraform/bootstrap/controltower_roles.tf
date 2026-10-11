# Service roles that CreateLandingZone requires in the management account.
# AWSControlTowerConfigAggregatorRoleForOrganizations is not required for landing zone 4.0.
# Attachments are non-exclusive: Control Tower attaches
# AWSControlTowerIdentityCenterManagementPolicy itself and Terraform leaves it alone.
locals {
  controltower_roles = {
    AWSControlTowerAdmin = {
      service             = "controltower.amazonaws.com"
      managed_policy_arns = ["arn:aws:iam::aws:policy/service-role/AWSControlTowerServiceRolePolicy"]
    }
    AWSControlTowerCloudTrailRole = {
      service             = "cloudtrail.amazonaws.com"
      managed_policy_arns = ["arn:aws:iam::aws:policy/service-role/AWSControlTowerCloudTrailRolePolicy"]
    }
    AWSControlTowerStackSetRole = {
      service             = "cloudformation.amazonaws.com"
      managed_policy_arns = []
    }
  }

  controltower_role_policy_attachments = {
    for pair in flatten([
      for role, config in local.controltower_roles : [
        for arn in config.managed_policy_arns : { key = "${role}/${basename(arn)}", role = role, arn = arn }
      ]
    ]) : pair.key => pair
  }
}

resource "aws_iam_role" "controltower" {
  for_each = local.controltower_roles

  name = each.key
  path = "/service-role/"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = each.value.service }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "controltower" {
  for_each = local.controltower_role_policy_attachments

  role       = aws_iam_role.controltower[each.value.role].name
  policy_arn = each.value.arn
}

resource "aws_iam_role_policy" "controltower_admin" {
  name = "AWSControlTowerAdminPolicy"
  role = aws_iam_role.controltower["AWSControlTowerAdmin"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ec2:DescribeAvailabilityZones"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "controltower_stackset" {
  name = "AWSControlTowerStackSetRolePolicy"
  role = aws_iam_role.controltower["AWSControlTowerStackSetRole"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = "arn:aws:iam::*:role/AWSControlTowerExecution"
    }]
  })
}
