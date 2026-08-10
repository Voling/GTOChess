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

# Managed policies rather than one of our own. A policy with every TTL at zero is
# "caching disabled", and CloudFront refuses a header whitelist in that mode:
# there is no cache key to put a header into. CachingDisabled says store nothing,
# AllViewerExceptHostHeader forwards the request whole, including Authorization.
# Nothing is cached, so nothing can be served to the wrong account.
locals {
  # CachingDisabled
  cache_none = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  # AllViewerExceptHostHeader: the ALB routes on path, not on the viewer's Host.
  forward_all = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.name}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The router. A deep link is a path with no object behind it, so it has to
# resolve to the document that boots the app. One millisecond at the edge, which
# is the whole reason this is a function and not Lambda@Edge.
resource "aws_cloudfront_function" "spa" {
  name    = "${local.name}-spa"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri.endsWith("/")) {
        request.uri = uri + "index.html";
      } else if (!uri.split("/").pop().includes(".")) {
        request.uri = "/index.html";
      }
      return request;
    }
  EOT
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = local.name
  aliases             = local.custom_domain ? [var.domain_name] : []
  default_root_object = "index.html"
  web_acl_id          = one(aws_wafv2_web_acl.edge[*].arn)

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

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    # CachingOptimized. Vite content hashes every asset, so they cache hard.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    compress        = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa.arn
    }
  }

  # Analysis is per position and per player, and the paid endpoints are POSTs
  # carrying a bearer token. None of it is cacheable, and caching a response
  # keyed without the token would hand one account's data to another.
  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = local.cache_none
    origin_request_policy_id = local.forward_all
    compress                 = true
  }

  # Kept reachable from the edge so a deploy can be checked from outside the VPC.
  # The target group checks the task directly and does not come through here.
  ordered_cache_behavior {
    path_pattern             = "/health"
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = local.cache_none
    origin_request_policy_id = local.forward_all
    compress                 = true
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

# A and AAAA both, because the distribution answers on IPv6 and an A record
# alone makes an IPv6 client wait for the fallback.
resource "aws_route53_record" "root" {
  for_each = local.custom_domain ? toset(["A", "AAAA"]) : toset([])

  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.this.domain_name
    zone_id                = aws_cloudfront_distribution.this.hosted_zone_id
    evaluate_target_health = false
  }
}
