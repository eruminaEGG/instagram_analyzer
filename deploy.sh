#!/usr/bin/env bash

set -euo pipefail
set +x

STAGE="production"

while getopts "s:" opt; do
  case "$opt" in
    s) STAGE="$OPTARG" ;;
    *)
      echo "Usage: $0 [-s stage]" >&2
      exit 1
      ;;
  esac
done

for name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY INSTAGRAM_ACCOUNT_ID INSTAGRAM_ACCESS_TOKEN; do
  if [[ -z "${!name:-}" ]]; then
    echo "Error: required environment variable is not set: $name" >&2
    exit 1
  fi
done

STACK_NAME="instagram-insights-${STAGE}"
AWS_REGION_VALUE="${AWS_REGION:-ap-northeast-1}"
CF_ERROR_FILE="$(mktemp)"
trap 'rm -f "$CF_ERROR_FILE"' EXIT

if STACK_STATUS="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION_VALUE" \
  --query 'Stacks[0].StackStatus' \
  --output text 2>"$CF_ERROR_FILE")"; then
  if [[ "$STACK_STATUS" == "ROLLBACK_COMPLETE" ]]; then
    echo "Error: $STACK_NAME is ROLLBACK_COMPLETE; preserve the DynamoDB table and resolve the stack state before deploying." >&2
    exit 1
  fi
elif ! grep -q 'does not exist' "$CF_ERROR_FILE"; then
  echo "Error: unable to inspect CloudFormation stack state for $STACK_NAME." >&2
  exit 1
fi

# AWS_SESSION_TOKEN is used automatically when it is present in the environment.
npx serverless deploy --stage "$STAGE" --region "$AWS_REGION_VALUE"
