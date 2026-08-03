variable "domain_name" {
  type        = string
  default     = ""
  description = "Leave empty to serve on the CloudFront domain and skip Route 53."
}

variable "hosted_zone_id" {
  type        = string
  default     = ""
  description = "Existing public zone for domain_name."
}

locals {
  custom_domain = var.domain_name != "" && var.hosted_zone_id != ""
}

# CloudFront only reads certificates from us-east-1, whatever region the rest of
# the stack runs in.
provider "aws" {
  alias  = "edge"
  region = "us-east-1"
}

resource "aws_acm_certificate" "this" {
  count             = local.custom_domain ? 1 : 0
  provider          = aws.edge
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = local.custom_domain ? {
    for option in aws_acm_certificate.this[0].domain_validation_options :
    option.domain_name => option
  } : {}

  zone_id = var.hosted_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 60

  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count                   = local.custom_domain ? 1 : 0
  provider                = aws.edge
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
}

resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.name}-api"
  default_ttl = 0
  min_ttl     = 0
  max_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Authorization"]
      }
    }

    query_strings_config {
      query_string_behavior = "all"
    }
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = local.name
  aliases         = local.custom_domain ? [var.domain_name] : []

  origin {
    domain_name = aws_lb.this.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    # CachingOptimized
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    compress        = true
  }

  # Analysis is per position and per player, and the paid endpoints are POSTs
  # carrying a bearer token. None of it is cacheable, and caching a response
  # keyed without the token would hand one account's data to another.
  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = aws_cloudfront_cache_policy.api.id
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.custom_domain ? false : true
    acm_certificate_arn            = local.custom_domain ? aws_acm_certificate_validation.this[0].certificate_arn : null
    ssl_support_method             = local.custom_domain ? "sni-only" : null
    minimum_protocol_version       = local.custom_domain ? "TLSv1.2_2021" : null
  }
}

resource "aws_route53_record" "root" {
  count   = local.custom_domain ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.this.domain_name
    zone_id                = aws_cloudfront_distribution.this.hosted_zone_id
    evaluate_target_health = false
  }
}
