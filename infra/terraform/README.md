# FiftyMoves on AWS

Route 53 and CloudFront at the edge, Cognito on the door, ECS underneath.

## Why not EKS

Scaling here is two independent problems and neither needs a Kubernetes control
plane. The API and the web tier scale on CPU through application autoscaling on
Fargate. The engine fleet scales through an ECS capacity provider over a spot
Auto Scaling group, and its unit of work is a Celery task, so Celery is already
doing the queueing, routing and retries that Kubernetes Jobs would duplicate.

EKS earns its keep when bin-packing a large heterogeneous fleet or when you want
one platform across many services. Until then it is a standing control plane,
an upgrade every few months, and roughly ten times the Terraform surface.

## Layout

| File | Holds |
|---|---|
| `network.tf` | VPC, public and private subnets, one NAT, security groups |
| `storage.tf` | ECR, EFS for the shared data directory, RDS Postgres, ElastiCache Redis |
| `cluster.tf` | ECS cluster, spot Auto Scaling group, capacity provider |
| `services.tf` | Task definitions and services for api, web, worker and measure |
| `alb.tf` | Internal load balancer and path routing |
| `edge.tf` | ACM, CloudFront, Route 53 |
| `cognito.tf` | User pool, hosted UI domain, public client |
| `iam.tf`, `secrets.tf` | Roles and the two secrets |

## First run

```sh
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
```

`plan` works with no domain set. `domain_name` and `hosted_zone_id` go together:
a certificate cannot validate without a zone that answers for the name.

Then push images to the two ECR repositories, set `image_tag`, and apply.

Put the Anthropic key in by hand so it never enters Terraform state:

```sh
aws secretsmanager put-secret-value \
  --secret-id fiftymoves-prod/anthropic-api-key \
  --secret-string sk-ant-...
```

## Accounts

Self signup is off. Every analysis is a model call on this account's bill, so a
user is invited rather than self-served:

```sh
aws cognito-idp admin-create-user \
  --user-pool-id "$(terraform output -raw cognito_user_pool_id)" \
  --username you@example.com
```

The API verifies the JWT itself rather than trusting a header from the edge, so
the ceiling holds even if something reaches the load balancer directly.

## Known gaps

**The daily analysis ceiling is per process.** `SpendLimiter` counts in memory
and the API runs two tasks, so the real ceiling is the limit times the task
count. Redis is already in the stack; moving the counter there closes it.

**EFS is a stopgap.** The JSONL stores are read by the API and written by the
workers, so today they need one shared filesystem. They belong in Postgres, and
that is also the prerequisite for fanning the sweep out one Celery task per
position instead of forking a pool inside a single task.

**One NAT gateway.** A single point of failure for egress. Add one per zone
before this carries traffic you cannot drop.

**HTTP between CloudFront and the load balancer.** The ALB listener is plain
HTTP inside the VPC. Terminate TLS there too before this leaves a trusted VPC.
