variable "project" {
  type    = string
  default = "fiftymoves"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  type        = number
  default     = 2
  description = "ALB needs two. Raising this widens the spot pool for the workers."
}

# Stockfish is CPU bound and its throughput swings with the core it lands on, so
# the family is pinned rather than left to Fargate. Diversify the list: a single
# instance type is a single spot pool and the first pool to tighten takes the
# whole fleet with it.
variable "worker_instance_types" {
  type    = list(string)
  default = ["c7a.2xlarge", "c7i.2xlarge", "c6a.2xlarge"]
}

variable "worker_min_size" {
  type    = number
  default = 0
}

variable "worker_max_size" {
  type    = number
  default = 4
}

variable "api_cpu" {
  type    = number
  default = 1024
}

variable "api_memory" {
  type    = number
  default = 2048
}

variable "web_cpu" {
  type    = number
  default = 256
}

variable "web_memory" {
  type    = number
  default = 512
}

variable "worker_cpu" {
  type        = number
  default     = 4096
  description = "Task size for the default queue. The measure queue takes a whole host."
}

variable "worker_memory" {
  type    = number
  default = 8192
}

variable "measure_cpu" {
  type        = number
  default     = 7168
  description = "Leaves headroom on a 2xlarge for the agent and the engine's own memory."
}

variable "measure_memory" {
  type    = number
  default = 14336
}

# A NAT gateway is roughly $33 a month before it moves a byte. Off, the tasks sit
# in public subnets and reach the internet directly; the task security group
# still admits nothing but the load balancer, so nothing new is exposed.
#
# Interface VPC endpoints are the usual replacement and are the wrong trade here:
# $0.01 per hour per endpoint per zone means ECR, Secrets Manager and Logs across
# two zones cost more than the gateway they replace.
variable "enable_nat_gateway" {
  type    = bool
  default = false
}

# Nothing reads a database yet. The stores are JSONL on the shared filesystem, so
# this stays off until they move and the schema exists.
variable "enable_database" {
  type    = bool
  default = false
}

# Spot is about 70% off and can be reclaimed on two minutes notice. At one task
# per service that means the odd gap while a replacement starts.
variable "use_fargate_spot" {
  type    = bool
  default = true
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "web_desired_count" {
  type    = number
  default = 1
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "engine_hash_mb" {
  type        = number
  default     = 256
  description = "Per engine process. Multiply by worker concurrency before sizing memory."
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "image_tag" {
  type    = string
  default = "latest"
}
