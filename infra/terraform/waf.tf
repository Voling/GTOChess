# A WebACL for CloudFront lives in us-east-1 whatever region the stack runs in,
# the same constraint as the certificate.
#
# What this is actually for: every /api route needs a verified token, so an
# anonymous flood already dies at the JWT check without reaching the engine.
# This stops that flood costing us Fargate CPU to reject.
#
# Roughly $5 a month for the ACL plus $1 per rule, so about $8 on a $45 stack.
# Set enable_waf false and the JWT gate is still the real control.
variable "enable_waf" {
  type    = bool
  default = true
}

variable "waf_api_rate_limit" {
  type        = number
  default     = 600
  description = "Requests per five minutes per IP against /api/. A signed in reader makes tens."
}

variable "waf_site_rate_limit" {
  type        = number
  default     = 2000
  description = "Requests per five minutes per IP everywhere else."
}

resource "aws_wafv2_web_acl" "edge" {
  count    = var.enable_waf ? 1 : 0
  provider = aws.edge
  name     = "${local.name}-edge"
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Tighter on the API than on the site, because an asset request is cheap and
  # a graph build is not.
  rule {
    name     = "api-rate"
    priority = 0

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_api_rate_limit
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "api-rate"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "site-rate"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_site_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "site-rate"
      sampled_requests_enabled   = true
    }
  }

  # Hosts already known for this. Cheaper to drop them here than to verify a
  # token for each one.
  rule {
    name     = "ip-reputation"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesAmazonIpReputationList"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-edge"
    sampled_requests_enabled   = true
  }
}
