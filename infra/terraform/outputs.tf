output "site_url" {
  value = local.custom_domain ? "https://${var.domain_name}" : "https://${aws_cloudfront_distribution.this.domain_name}"
}

output "load_balancer_dns" {
  value = aws_lb.this.dns_name
}

output "ecr_backend" {
  value = aws_ecr_repository.backend.repository_url
}

output "ecr_web" {
  value = aws_ecr_repository.web.repository_url
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

output "database_endpoint" {
  value = one(aws_db_instance.this[*].address)
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "data_filesystem_id" {
  value = aws_efs_file_system.data.id
}
