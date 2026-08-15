output "site_url" {
  value = "https://${var.domain_name}"
}

output "public_ip" {
  value = aws_eip.this.public_ip
}

output "instance_id" {
  description = "aws ssm start-session --target this"
  value       = aws_instance.this.id
}

output "ecr_backend" {
  value = aws_ecr_repository.backend.repository_url
}

output "site_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.this.id
}

output "cognito_hosted_ui" {
  value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
}
