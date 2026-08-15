# GTO Chess on one box

The whole application on a single instance: Caddy for TLS and the site, the API,
one Celery worker, and Redis as the queue between them. About **$35 a month**.

This exists alongside `../terraform/`, which is the ECS version. Both apply from
their own directory and their own state, so switching is one `terraform apply`
in the other place. Use this one until there is load that justifies the other.

## What it costs

| | monthly |
|---|---|
| `t3.medium` on demand | $30 |
| 30 GB gp3 | $2.40 |
| Route 53 zone | $0.50 |
| S3, ECR, Secrets Manager | ~$1 |
| **total** | **~$34** |

Spot takes the instance to about $9, at the price of the site going down for a
few minutes when it is reclaimed.

## What it does not have

No load balancer, no CloudFront, no ElastiCache, no ECS, no WAF. Caddy takes a
certificate from Let's Encrypt on first boot and serves the built frontend
itself, so TLS and the CDN layer cost nothing.

That means no zero downtime deploys: a redeploy is a container restart. It also
means one instance is the whole tier, so losing it is an outage rather than a
degraded service. Both are fine while nobody is paying.

There is **no SSH rule and no key pair**. A shell comes from Session Manager:

```sh
aws ssm start-session --target "$(terraform output -raw instance_id)"
```

## What is not on the box

Everything durable. The stores are in S3 behind the application's `Storage`
interface, the image is in ECR, and the Anthropic key is in Secrets Manager and
read at boot into a `.env` that never enters Terraform state. Replacing the
instance is a restart, not a loss.

The one thing that is genuinely local is Redis, and only because it is a queue
rather than a record. It keeps an append only file on a volume so a restart does
not drop queued imports, and runs `noeviction` because shedding a job under
memory pressure would lose work silently.

## First run

```sh
cp terraform.tfvars.example terraform.tfvars   # already filled in for gtochess.com
terraform init
terraform apply
```

Then, in order:

```sh
# 1. the model key, by hand so it never enters state
aws secretsmanager put-secret-value \
  --secret-id gtochess-lite/anthropic-api-key --secret-string sk-ant-...

# 2. the image, or the containers have nothing to run
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "$(terraform output -raw ecr_backend | cut -d/ -f1)"
docker build --platform linux/amd64 --target runtime -t "$(terraform output -raw ecr_backend):latest" ../../backend
docker push "$(terraform output -raw ecr_backend):latest"

# 3. the frontend
(cd ../../frontend && npm ci && npm run build)
aws s3 sync ../../frontend/dist "s3://$(terraform output -raw site_bucket)" --delete

# 4. your data, which is still only on your machine
aws s3 sync ../../backend/data "s3://$(terraform output -raw data_bucket)"

# 5. an account, because self signup is off
PROJECT=gtochess ENVIRONMENT=lite ../add-user.sh you@example.com
```

## Redeploying

The bootstrap leaves a script on the box that does the whole thing:

```sh
aws ssm send-command \
  --document-name AWS-RunShellScript \
  --targets "Key=instanceids,Values=$(terraform output -raw instance_id)" \
  --parameters 'commands=["/usr/local/bin/gtochess-redeploy"]'
```

It re-authenticates to ECR, syncs the site down, pulls, and restarts. Changing
`user_data` replaces the instance instead, which is also safe.

## Sweeps do not belong here

Two vCPUs. A 3,597 position sweep took thirty minutes on 24 workers; here it
would take about six hours. Keep running `gtochess-measure` on a machine with
cores and sync the loss store up:

```sh
aws s3 cp ../../backend/data/move_costs.jsonl "s3://$(terraform output -raw data_bucket)/"
```

The `measure` queue and its spot capacity provider in `../terraform/` exist for
exactly this and are worth going back to when sweeping gets frequent.

## Known gaps

**Nothing backs up Redis off the box.** Queued jobs survive a restart but not the
instance. Losing them means re-pressing import, which is cheap.

Redis now holds the spend ceiling and the lichess OAuth handoff as well as the
queue, so both stay right above one API container. If it is unreachable the API
keeps answering with a per process count and says so on `/health`, which reads
`redis (degraded to memory)` rather than `redis`.

**The account cache is still per process**, held for 30 seconds. A link made on
one container is refused on another until that expires.
