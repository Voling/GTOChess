#!/usr/bin/env bash
# Issues an account. Self signup is off, so this is the only door.
#
#   ./infra/add-user.sh you@example.com
#
# Prints a generated password once and never stores it. The password is set as
# permanent, so there is no forced change on first sign in and no temporary
# credential sitting in an inbox.
#
# Deliberately not terraform: a password in a resource argument is a password in
# state, readable by anyone who can read the bucket.
set -euo pipefail

EMAIL="${1:-}"
PROJECT="${PROJECT:-gtochess}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
REGION="${REGION:-us-east-1}"

if [ -z "$EMAIL" ]; then
  echo "usage: $0 <email>" >&2
  exit 1
fi

POOL="$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  --query "UserPools[?Name=='${PROJECT}-${ENVIRONMENT}'].Id | [0]" --output text)"

if [ -z "$POOL" ] || [ "$POOL" = "None" ]; then
  echo "no user pool named ${PROJECT}-${ENVIRONMENT} in ${REGION}. Apply the stack first." >&2
  exit 1
fi

# 32 bytes of urandom, filtered to a set the pool's policy accepts, then given
# one of each required class so it cannot fail validation by chance.
body="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
PASSWORD="Aa1${body}"

aws cognito-idp admin-create-user \
  --region "$REGION" \
  --user-pool-id "$POOL" \
  --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS >/dev/null

aws cognito-idp admin-set-user-password \
  --region "$REGION" \
  --user-pool-id "$POOL" \
  --username "$EMAIL" \
  --password "$PASSWORD" \
  --permanent

cat <<TEXT

pool      ${POOL}
user      ${EMAIL}
password  ${PASSWORD}

Shown once. Put it in a password manager now, because nothing here can read it
back and the only recovery is to run this again.
TEXT
