# GTO Chess on AWS

Route 53 and CloudFront at the edge, Cognito on the door, ECS underneath.

## Why not EKS

ECS charges nothing for its control plane. EKS charges $0.10 an hour, about $73 a
month, and the compute underneath bills the same either way, so EKS here is ECS
plus a subscription. Karpenter can beat an ECS capacity provider on packing, but
it earns that on a large heterogeneous fleet; this is four services and a burst
of engine workers, and there is nothing to pack.

The scaling is also two independent problems and neither needs a Kubernetes
control plane. The API and the web tier scale on CPU through application
autoscaling on Fargate. The engine fleet scales through an ECS capacity provider
over a spot Auto Scaling group, and its unit of work is a Celery task, so Celery
already does the queueing, routing and retries that Kubernetes Jobs would
duplicate.

## What it costs

Roughly $45 a month idle, in us-east-1 at list price:

| | monthly |
|---|---|
| ALB | $16 |
| ElastiCache `cache.t4g.micro` | $12 |
| api, one task on Fargate Spot | $11 |
| S3, EFS, logs, ECR | ~$3 |

The frontend is not on that list. It is built to static files, synced to a
private bucket, and served by CloudFront through an origin access control, so it
runs no container and touches the load balancer only for `/api/*`.

Three variables move that number, and all three ship set to the cheap side.

**`enable_nat_gateway`, off.** A gateway is about $33 a month before it moves a
byte. Off, the tasks run in public subnets with public addresses; the task
security group still admits nothing but the load balancer, so nothing new is
reachable. Interface VPC endpoints are the usual replacement and are the wrong
trade at this size: $0.01 per hour per endpoint per zone means ECR, Secrets
Manager and Logs across two zones cost more than the gateway they replace.

Turning the gateway on moves every task back into the private subnets, so it is
the one switch to flip before this holds anything worth protecting.

**`enable_database`, off.** Nothing reads a database yet. The stores are JSONL on
the shared filesystem, so RDS was $47 a month for an empty instance.

**`use_fargate_spot`, on.** About 70% off, reclaimed on two minutes notice. At
one task per service that means the occasional gap while a replacement starts.
Set it false, or raise `api_desired_count`, when a gap stops being acceptable.

The engine fleet is not on this list because it is not the cost driver. A full
sweep of a 3,600 position repertoire is about 13 core hours, which is roughly 22
cents of spot. The model is the expensive half: see `GTOCHESS_ANALYSIS_DAILY_LIMIT`
in `backend/.env.example`, which is the ceiling on what one account can spend.

## Layout

| File | Holds |
|---|---|
| `network.tf` | VPC, public and private subnets, optional NAT, S3 endpoint, security groups |
| `storage.tf` | ECR, the site bucket, EFS, optional RDS and object storage, ElastiCache Redis |
| `cluster.tf` | ECS cluster, spot Auto Scaling group, capacity provider |
| `services.tf` | Task definitions and services for api, worker and measure |
| `alb.tf` | Load balancer, which carries the API and nothing else |
| `edge.tf` | ACM, CloudFront, the SPA function, Route 53 |
| `cognito.tf` | User pool, hosted UI domain, public client |
| `iam.tf`, `secrets.tf` | Roles and the secrets |

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
  --secret-id gtochess-prod/anthropic-api-key \
  --secret-string sk-ant-...
```

## From CI

`.github/workflows/terraform.yml` runs plan, apply and destroy, and is manual
only. Nothing in it fires off a push: the stack owns a spending ceiling, a user
pool and a spot fleet, and none of those should move because someone edited a
component.

`.github/workflows/deploy.yml` is the other half and is also manual. Landing on
main is not a decision to ship, so it runs from the Run workflow button with a
choice of `both`, `web` or `backend`.

The two halves of the app deploy differently. `backend` builds an image, pushes
`:latest` and `:<sha>`, and forces a new deployment; task definitions name
`:latest`, so that never needs Terraform. `web` builds static files and syncs
them to the site bucket, then invalidates `/` and `/index.html` at the edge. The
hashed assets are immutable by name and never need invalidating.

Run it from the default branch. The deploy role trusts
`repo:<owner>/GTOChess:ref:refs/heads/main` and nothing else, so a dispatch from
another branch cannot authenticate.

Every value is a repository secret with a working default, so the stack comes up
with only credentials set.

| Secret | Default | Used by |
|---|---|---|
| `AWS_ROLE_ARN` | — | deploy, and terraform if the next one is unset |
| `AWS_TERRAFORM_ROLE_ARN` | falls back to `AWS_ROLE_ARN` | terraform |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | — | both, if not using OIDC |
| `AWS_REGION` | `us-east-1` | both |
| `TF_STATE_BUCKET` | — | terraform; unset means local state, fine for a plan and wrong for an apply |
| `TF_STATE_KEY` | `gtochess/prod/terraform.tfstate` | terraform |
| `TF_STATE_LOCK_TABLE` | — | terraform; unset locks with S3 itself |
| `TERRAFORM_VERSION` | `1.10.5` | terraform |
| `TF_VAR_DOMAIN_NAME`, `TF_VAR_HOSTED_ZONE_ID` | — | terraform |
| `TF_VAR_PROJECT`, `TF_VAR_ENVIRONMENT` | `gtochess`, `prod` | terraform |
| `TF_VAR_ENABLE_NAT_GATEWAY`, `TF_VAR_ENABLE_DATABASE`, `TF_VAR_USE_FARGATE_SPOT` | as shipped | terraform |
| `TF_VAR_WORKER_MAX_SIZE` | `4` | terraform |
| `ECR_BACKEND` | `gtochess-prod-backend` | deploy |
| `SITE_BUCKET` | `gtochess-prod-site` | deploy |
| `ECS_CLUSTER` | `gtochess-prod` | deploy |
| `ECS_SERVICE_API`, `ECS_SERVICE_WORKER` | `gtochess-prod-*` | deploy |
| `CLOUDFRONT_DISTRIBUTION_ID` | — | deploy; unset skips the invalidation |

Apply and destroy run in the `terraform-apply` environment. Add a required
reviewer to it in the repository settings and both stop for approval; plan uses
`terraform-plan` and needs no gate.

## Roles

`../bootstrap.sh` creates both, and is the only thing here that cannot be made
by the stack it makes. Rerunning it is safe.

```sh
REPO=<owner>/GTOChess ./infra/bootstrap.sh
```

Two roles rather than one, because the two workflows want very different
things. Terraform is manual, rare, and has to create IAM. Deploy runs on every
push to main and only needs to push one image, roll two services, replace the
site objects and invalidate one path.

Each is scoped to the job context rather than to the repository, which is the
part worth reading. The terraform role is assumable only from the
`terraform-plan` and `terraform-apply` environments, and the deploy role only
from a push to `main`. A pull request from a fork carries neither subject, so it
can assume neither role however the workflow file is edited in that fork.

The terraform role gets `PowerUserAccess`, which covers every service in the
stack and deliberately excludes IAM, plus an inline policy adding back exactly
the IAM terraform uses, restricted to `gtochess-*` names.

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

**The daily analysis ceiling is per process.** `SpendLimiter` counts in memory,
so the real ceiling is the limit times the task count. At one API task that is
exact; it stops being exact the moment autoscaling adds a second. Redis is
already in the stack, and moving the counter there closes it.

**EFS is a stopgap.** The JSONL stores are read by the API and written by the
workers, so today they need one shared filesystem. They belong in Postgres, and
that is also the prerequisite for fanning the sweep out one Celery task per
position instead of forking a pool inside a single task.

**Workers run in bridge network mode.** With the NAT off, an EC2 task ENI takes
no public address and would have no route out, so the worker and measure tasks
share the instance's network instead of holding their own. They get the
instance's security group with it.

**HTTP between CloudFront and the load balancer.** The ALB listener is plain
HTTP inside the VPC. Terminate TLS there too before this leaves a trusted VPC.
