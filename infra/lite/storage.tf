resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

# Every store the application keeps. Not on the instance's disk: the box is
# replaceable and the data is not.
resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-data"
}

# The built frontend. Caddy serves it from the instance, which syncs it down on
# boot, so this is the source of truth rather than the thing being served.
resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-site"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Every store is read whole and put whole, so a bad put is recoverable only if
# the version before it survives.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket     = aws_s3_bucket.data.id
  depends_on = [aws_s3_bucket_versioning.data]

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_secretsmanager_secret" "anthropic" {
  name                    = "${local.name}/anthropic-api-key"
  description             = "Unset leaves the app on its deterministic provider rather than failing"
  recovery_window_in_days = 0
}

# --- accounts ---------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  name = local.name

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  # Self signup stays off. Every analysis is a model call on this account's
  # bill, so a user is invited rather than self-served.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  # Cognito already locks an account out after five failed sign ins, backing off
  # to about fifteen minutes. This blocks credentials known to be breached on
  # top of that, billed per monthly active user.
  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.name
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "this" {
  name         = local.name
  user_pool_id = aws_cognito_user_pool.this.id

  # Public client: the browser holds no secret, so PKCE carries the exchange.
  generate_secret = false

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = [
    "http://localhost:5173/auth/callback",
    "https://${var.domain_name}/auth/callback",
  ]

  logout_urls = [
    "http://localhost:5173/",
    "https://${var.domain_name}/",
  ]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  # USER_PASSWORD_AUTH so the sign in form is ours rather than Cognito's hosted
  # page. The password goes straight from the browser to Cognito over TLS and
  # never touches our servers.
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}
