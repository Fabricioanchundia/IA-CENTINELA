#!/usr/bin/env bash
# Cloud drill (Phase 10): run the end-to-end test on a short-lived EC2 instance, then delete it.
#
#   ./scripts/cloud-drill.sh               test the main branch
#   ./scripts/cloud-drill.sh feat/cicd     test another branch or tag
#
# COST: one m7i-flex.large for about 30-60 minutes, roughly USD 0.30-0.50 of AWS credit.
# The instance has no inbound ports, no SSH key and no AWS credentials, and terminates itself
# after 120 minutes even if this script is interrupted; `terraform destroy` runs on exit anyway.
# Needs: an AWS CLI profile "centinela" for an IAM user that has only infra/aws/iam-policy.json
# (EC2 in us-east-2, only m7i-flex.large). Another profile: AWS_PROFILE=name ./scripts/cloud-drill.sh
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-centinela}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TF="terraform -chdir=$ROOT/infra/aws"
REF=${1:-main}
LIMIT_MIN=${LIMIT_MIN:-110}
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

aws sts get-caller-identity >/dev/null || { echo "no credentials for profile $AWS_PROFILE"; exit 1; }
echo "AWS credentials OK (profile $AWS_PROFILE)"

step "Creating the instance (branch: $REF)"
$TF init -input=false >/dev/null
cleanup() {
  step "Deleting everything (terraform destroy)"
  $TF destroy -input=false -auto-approve -var "git_ref=$REF" | tail -n 1
}
trap cleanup EXIT
$TF apply -input=false -auto-approve -var "git_ref=$REF" | tail -n 3
id=$($TF output -raw instance_id)
region=$($TF output -raw region)

step "Following the drill on the serial console (updates every few minutes)"
seen=0
deadline=$(( $(date +%s) + LIMIT_MIN * 60 ))
result=""
while (( $(date +%s) < deadline )); do
  sleep 30
  lines=$(aws ec2 get-console-output --instance-id "$id" --region "$region" --latest \
    --output text --query Output 2>/dev/null | tr -d '\r' | grep -a 'CENTINELA-DRILL' || true)
  total=$(printf '%s\n' "$lines" | grep -c . || true)
  if (( total > seen )); then
    printf '%s\n' "$lines" | tail -n $(( total - seen )) | sed 's/^.*CENTINELA-DRILL /  /'
    seen=$total
  fi
  if grep -q 'CENTINELA-DRILL [0-9:]* RESULT' <<<"$lines"; then
    result=$(grep -o 'RESULT .*' <<<"$lines" | tail -n 1)
    break
  fi
done

step "${result:-RESULT unknown: time limit reached}"
[[ "$result" == "RESULT PASSED" ]]
