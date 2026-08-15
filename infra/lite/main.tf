terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name = "${var.project}-${var.environment}"
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
}

# --- network ----------------------------------------------------------------
# One public subnet. No NAT, no second zone, no load balancer: the instance is
# the whole tier and it answers on its own address.
resource "aws_vpc" "this" {
  cidr_block           = "10.50.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = "10.50.0.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = { Name = local.name }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = local.name }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# 80 is open only because Let's Encrypt validates over it. There is no SSH rule:
# a shell comes from Session Manager, which needs no open port and no key.
resource "aws_security_group" "instance" {
  name        = local.name
  description = "Public web, no inbound shell"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTP, for the ACME challenge and the redirect to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = local.name }
}

# --- identity ---------------------------------------------------------------
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# A shell without an open port or a private key to lose.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "instance" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/*", "${aws_s3_bucket.site.arn}/*"]
  }

  # head_object on an absent key answers 403 without this, so "has this player
  # been imported" would read as a permissions failure rather than a no.
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn, aws_s3_bucket.site.arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.anthropic.arn]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = local.name
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = local.name
  role = aws_iam_role.instance.name
}

# --- the box ----------------------------------------------------------------
resource "aws_eip" "this" {
  domain = "vpc"
  tags   = { Name = local.name }
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required"
  }

  user_data = templatefile("${path.module}/cloud-init.sh.tftpl", {
    region               = var.region
    registry             = split("/", aws_ecr_repository.backend.repository_url)[0]
    image                = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    domain_name          = var.domain_name
    acme_email           = var.acme_email
    site_bucket          = aws_s3_bucket.site.bucket
    data_bucket          = aws_s3_bucket.data.bucket
    anthropic_secret     = aws_secretsmanager_secret.anthropic.arn
    cognito_pool         = aws_cognito_user_pool.this.id
    cognito_client       = aws_cognito_user_pool_client.this.id
    cognito_domain       = aws_cognito_user_pool_domain.this.domain
    worker_concurrency   = var.worker_concurrency
    engine_hash_mb       = var.engine_hash_mb
    analysis_daily_limit = var.analysis_daily_limit
  })

  # Changing user_data rebuilds the box. Everything on it is either in S3 or in
  # the image, so that is a restart rather than a loss.
  user_data_replace_on_change = true

  lifecycle {
    # The AMI data source resolves to whatever Amazon published most recently,
    # and ami is ForceNew. Without this, an unrelated apply replaces the
    # instance, which discards Caddy's certificate store and Redis's append log.
    # Five replacements in a week hits Let's Encrypt's duplicate limit and the
    # site then serves an untrusted certificate. Replace it deliberately with
    # -replace instead.
    ignore_changes = [ami]
  }

  # Without these the instance can launch in parallel with its own permissions,
  # and the bootstrap dies on `docker login` before it starts anything.
  depends_on = [
    aws_iam_role_policy.instance,
    aws_iam_role_policy_attachment.ssm,
    aws_iam_role_policy_attachment.ecr,
  ]

  tags = { Name = local.name }
}

resource "aws_eip_association" "this" {
  instance_id   = aws_instance.this.id
  allocation_id = aws_eip.this.id
}

# --- dns --------------------------------------------------------------------
resource "aws_route53_record" "root" {
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 60
  records = [aws_eip.this.public_ip]
}
