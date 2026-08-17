data "aws_iam_policy_document" "github_deploy_cloudfront" {
  statement {
    sid = "CloudFrontManagedPolicyRead"
    actions = [
      "cloudfront:GetCachePolicy",
      "cloudfront:GetCachePolicyConfig",
      "cloudfront:GetOriginRequestPolicy",
      "cloudfront:GetOriginRequestPolicyConfig",
      "cloudfront:ListCachePolicies",
      "cloudfront:ListOriginRequestPolicies",
    ]
    resources = ["*"]
  }

  statement {
    sid = "CreateTaggedRecordingDistribution"
    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:TagResource",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Application"
      values   = [var.project_name]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = [var.environment]
    }
  }

  statement {
    sid = "ReadAccountDistributions"
    actions = [
      "cloudfront:GetDistribution",
      "cloudfront:GetDistributionConfig",
      "cloudfront:ListTagsForResource",
    ]
    resources = [
      "arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/*",
    ]
  }

  statement {
    sid = "ManageTaggedRecordingDistribution"
    actions = [
      "cloudfront:DeleteDistribution",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:UpdateDistribution",
    ]
    resources = [
      "arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Application"
      values   = [var.project_name]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [var.environment]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy_cloudfront" {
  name   = "${local.name_prefix}-cloudfront-recording"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy_cloudfront.json
}
