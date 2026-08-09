region      = "us-east-1"
project     = "gtochess"
environment = "prod"

domain_name    = "gtochess.com"
hosted_zone_id = "Z01814262NV51RS56NGUR"

# The switches that decide the standing bill. As set, the stack idles around $45
# a month; turning the gateway and the database on and taking the API off spot
# takes it past $200.
enable_nat_gateway    = false
enable_database       = false
enable_object_storage = false
use_fargate_spot      = true

api_desired_count = 1

# Diversify. One instance type is one spot pool, and the first pool to tighten
# takes the whole fleet with it.
worker_instance_types = ["c7a.2xlarge", "c7i.2xlarge", "c6a.2xlarge"]
worker_min_size       = 0
worker_max_size       = 4

redis_node_type = "cache.t4g.micro"

# Moved by CI to the image it just pushed.
image_tag = "latest"
