resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# The built frontend. Private: CloudFront reaches it through an origin access
# control, and nothing else can.
resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-site"
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "site" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

# Only when the stores are not in S3. Every store goes through the Storage
# interface now, so with object storage on nothing reads or writes a filesystem
# and this whole block has no reason to exist.
resource "aws_efs_file_system" "data" {
  count          = local.uses_efs ? 1 : 0
  creation_token = "${local.name}-data"
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = { Name = "${local.name}-data" }
}

resource "aws_efs_mount_target" "data" {
  count           = local.uses_efs ? var.az_count : 0
  file_system_id  = aws_efs_file_system.data[0].id
  subnet_id       = local.egress_subnets[count.index]
  security_groups = [aws_security_group.data.id]
}

resource "aws_efs_access_point" "data" {
  count          = local.uses_efs ? 1 : 0
  file_system_id = aws_efs_file_system.data[0].id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0755"
    }
  }

  tags = { Name = "${local.name}-data" }
}

# The stores behind the Storage interface. On, they leave the shared filesystem
# and EFS is only holding the games export; off, everything stays on EFS.
resource "aws_s3_bucket" "data" {
  count  = var.enable_object_storage ? 1 : 0
  bucket = "${local.name}-data"
}

resource "aws_s3_bucket_public_access_block" "data" {
  count                   = var.enable_object_storage ? 1 : 0
  bucket                  = aws_s3_bucket.data[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  count  = var.enable_object_storage ? 1 : 0
  bucket = aws_s3_bucket.data[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Every store is read whole and put whole, so a bad put is recoverable only if
# the version before it survives.
resource "aws_s3_bucket_versioning" "data" {
  count  = var.enable_object_storage ? 1 : 0
  bucket = aws_s3_bucket.data[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  count      = var.enable_object_storage ? 1 : 0
  bucket     = aws_s3_bucket.data[0].id
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

resource "aws_db_subnet_group" "this" {
  count      = var.enable_database ? 1 : 0
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "random_password" "db" {
  count   = var.enable_database ? 1 : 0
  length  = 32
  special = false
}

resource "aws_db_instance" "this" {
  count          = var.enable_database ? 1 : 0
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 4
  storage_encrypted     = true

  db_name  = var.project
  username = var.project
  password = random_password.db[0].result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this[0].name
  vpc_security_group_ids = [aws_security_group.data.id]

  backup_retention_period   = 7
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"
  deletion_protection       = true

  # pgvector ships with RDS Postgres; the extension still has to be created in
  # the database once, which the application migration does.
  parameter_group_name = aws_db_parameter_group.this[0].name

  tags = { Name = local.name }
}

resource "aws_db_parameter_group" "this" {
  count  = var.enable_database ? 1 : 0
  name   = local.name
  family = "postgres16"

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
}

resource "aws_elasticache_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.name
  description          = "Celery broker and result backend"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = 1
  automatic_failover_enabled = false

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.data.id]

  at_rest_encryption_enabled = true

  tags = { Name = local.name }
}
