variable "project" {
  type    = string
  default = "gtochess"
}

variable "environment" {
  type    = string
  default = "lite"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "domain_name" {
  type        = string
  description = "Caddy takes a certificate for this from Let's Encrypt on first boot."
}

variable "hosted_zone_id" {
  type        = string
  description = "Existing public zone for domain_name. Terraform writes records, not the zone."
}

variable "acme_email" {
  type        = string
  description = "Let's Encrypt sends expiry warnings here."
}

# Two vCPU and 4 GB. Enough for the API, one worker and Redis at once. Not
# enough for a sweep: 3,597 positions on two cores is about six hours, so keep
# running those on a machine with cores to spare.
variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "volume_gb" {
  type    = number
  default = 30
}

variable "image_tag" {
  type    = string
  default = "latest"
}

# Concurrency for the default queue. One import or annotation at a time on two
# cores; raising it just makes both slower.
variable "worker_concurrency" {
  type    = number
  default = 1
}

variable "engine_hash_mb" {
  type    = number
  default = 256
}

variable "analysis_daily_limit" {
  type    = number
  default = 10
}
