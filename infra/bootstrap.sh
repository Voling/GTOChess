#!/usr/bin/env bash
# Creates the two roles GitHub Actions assumes. Idempotent: safe to rerun after
# renaming the repository or changing the project prefix.
#
#   ./infra/bootstrap.sh
#
# Everything else the workflows need is a name with a working default. These two
# roles are the only thing that cannot be created by the stack they create.
#
# Policies are passed inline rather than as file:// so this runs the same under
# Git Bash on Windows, where a POSIX temp path would not survive translation.
set -euo pipefail

# Space separated, so a role keeps working across a repository rename. Drop the
# old name once the rename has happened.
REPOS="${REPOS:-Voling/GTOChess}"
PROJECT="${PROJECT:-gtochess}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
# Not the CLI default: this has to match var.region in the stack.
REGION="${REGION:-us-east-1}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
OIDC="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
NAME="${PROJECT}-${ENVIRONMENT}"
TF_ROLE="${NAME}-terraform"
DEPLOY_ROLE="${NAME}-deploy"

echo "account ${ACCOUNT}, prefix ${NAME}, repos: ${REPOS}"

if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC" >/dev/null 2>&1; then
  echo "creating the GitHub OIDC provider"
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
fi

# Subject lists, one entry per repository per context.
subjects() {
  local suffix="$1" out="" repo
  for repo in $REPOS; do
    out="${out:+${out}, }\"repo:${repo}:${suffix}\""
  done
  printf '%s' "$out"
}
TF_SUBJECTS="$(subjects 'environment:terraform-plan'), $(subjects 'environment:terraform-apply')"
DEPLOY_SUBJECTS="$(subjects 'ref:refs/heads/main')"

# --- trust ------------------------------------------------------------------
# Scoped to the job context, not just the repository. Terraform is reachable
# only from the two environments terraform.yml declares, and the deploy role
# only from a push to the default branch. A pull request from a fork carries
# neither subject, so it can assume neither role.
TRUST_TERRAFORM=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "${OIDC}" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": [${TF_SUBJECTS}] }
    }
  }]
}
JSON
)

TRUST_DEPLOY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "${OIDC}" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": [${DEPLOY_SUBJECTS}] }
    }
  }]
}
JSON
)

# --- terraform permissions --------------------------------------------------
# PowerUserAccess covers every service in the stack and deliberately excludes
# IAM, which terraform does need. This adds back exactly the IAM it uses, on the
# project's own names, so a bug in a module cannot touch an unrelated role.
TERRAFORM_IAM=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageProjectRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole",
        "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags",
        "iam:UpdateAssumeRolePolicy",
        "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy", "iam:ListRolePolicies",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
        "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
        "iam:TagInstanceProfile", "iam:ListInstanceProfilesForRole",
        "iam:PassRole"
      ],
      "Resource": [
        "arn:aws:iam::${ACCOUNT}:role/${PROJECT}-*",
        "arn:aws:iam::${ACCOUNT}:instance-profile/${PROJECT}-*"
      ]
    },
    {
      "Sid": "ReadAWSManagedPolicies",
      "Effect": "Allow",
      "Action": ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions"],
      "Resource": "arn:aws:iam::aws:policy/*"
    }
  ]
}
JSON
)

# --- deploy permissions -----------------------------------------------------
# Everything deploy.yml does and nothing else: push one image, roll two
# services, replace the site objects, forget the edge copy of index.html.
DEPLOY_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrLogin",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "PushBackendImage",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"
      ],
      "Resource": "arn:aws:ecr:${REGION}:${ACCOUNT}:repository/${NAME}-backend"
    },
    {
      "Sid": "RollServices",
      "Effect": "Allow",
      "Action": ["ecs:UpdateService", "ecs:DescribeServices"],
      "Resource": [
        "arn:aws:ecs:${REGION}:${ACCOUNT}:service/${NAME}/${NAME}-api",
        "arn:aws:ecs:${REGION}:${ACCOUNT}:service/${NAME}/${NAME}-worker"
      ]
    },
    {
      "Sid": "PublishSite",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:DeleteObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::${NAME}-site/*"
    },
    {
      "Sid": "ListSiteForSync",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${NAME}-site"
    },
    {
      "Sid": "ForgetTheEdgeCopy",
      "Effect": "Allow",
      "Action": ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
      "Resource": "arn:aws:cloudfront::${ACCOUNT}:distribution/*"
    }
  ]
}
JSON
)

upsert_role() {
  local role="$1" trust="$2"
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$role" --policy-document "$trust"
    echo "updated trust on $role"
  else
    aws iam create-role --role-name "$role" \
      --assume-role-policy-document "$trust" \
      --description "GitHub Actions for ${PROJECT}" >/dev/null
    echo "created $role"
  fi
}

upsert_role "$TF_ROLE" "$TRUST_TERRAFORM"
upsert_role "$DEPLOY_ROLE" "$TRUST_DEPLOY"

aws iam attach-role-policy --role-name "$TF_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/PowerUserAccess
aws iam put-role-policy --role-name "$TF_ROLE" \
  --policy-name "${NAME}-terraform-iam" --policy-document "$TERRAFORM_IAM"
aws iam put-role-policy --role-name "$DEPLOY_ROLE" \
  --policy-name "${NAME}-deploy" --policy-document "$DEPLOY_POLICY"

cat <<TEXT

Set these repository secrets under Settings, Secrets and variables, Actions:

  AWS_TERRAFORM_ROLE_ARN  arn:aws:iam::${ACCOUNT}:role/${TF_ROLE}
  AWS_ROLE_ARN            arn:aws:iam::${ACCOUNT}:role/${DEPLOY_ROLE}

The terraform role is assumable only from the terraform-plan and
terraform-apply environments, so create both under Settings, Environments, and
put a required reviewer on terraform-apply.
TEXT
