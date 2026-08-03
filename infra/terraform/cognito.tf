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

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Self signup stays off. Every paid analysis is charged to this account, so the
  # gate is an invite rather than a form anyone can fill in.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.name
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "this" {
  name         = local.name
  user_pool_id = aws_cognito_user_pool.this.id

  # Public client: the SPA holds no secret, so PKCE carries the exchange.
  generate_secret = false

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = compact([
    "http://localhost:5173/auth/callback",
    var.domain_name != "" ? "https://${var.domain_name}/auth/callback" : "",
  ])

  logout_urls = compact([
    "http://localhost:5173/",
    var.domain_name != "" ? "https://${var.domain_name}/" : "",
  ])

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}
