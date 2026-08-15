region      = "us-east-1"
project     = "gtochess"
environment = "lite"

domain_name    = "gtochess.com"
hosted_zone_id = "Z01814262NV51RS56NGUR"
acme_email     = "dethanvo@gmail.com"

# Two vCPU, 4 GB. Not sized for sweeps; run those where there are cores.
instance_type = "t3.medium"
volume_gb     = 30

worker_concurrency   = 1
analysis_daily_limit = 10
image_tag            = "latest"
