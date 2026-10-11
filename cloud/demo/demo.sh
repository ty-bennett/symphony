#!/usr/bin/env bash
# Symphony live demo helper: a laptop stands in for the Jetson (mTLS device cert +
# Cognito user) and calls the tenant endpoint. See cloud/demo/README.md.
#
#   cloud/demo/demo.sh setup                      # once: cache endpoint values
#   cloud/demo/demo.sh warm                       # 2 minutes before presenting
#   cloud/demo/demo.sh ask alice "What is our PTO policy?"
#   cloud/demo/demo.sh ask alice "..." --model step-up --data-tier frontier-eligible
#   cloud/demo/demo.sh audit 5                    # last audit lines (no text)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$HOME/.symphony"
ENV_FILE="$STATE/demo.env"
export AWS_PROFILE="${AWS_PROFILE:-iamadmin-general}"
UV=(uv run --quiet --no-project --with 'boto3>=1.43.111' --with 'cryptography>=46')

load() {
  [ -f "$ENV_FILE" ] || { echo "Run: $0 setup" >&2; exit 1; }
  # shellcheck disable=SC1090
  source "$ENV_FILE"
}

user_email() {
  local name="$1"
  case "$name" in
    *@*) echo "$name" ;;
    *)
      local var
      var="DEMO_USER_$(echo "$name" | tr '[:lower:]' '[:upper:]')"
      echo "${!var:?unknown demo user $name; add $var to $ENV_FILE}"
      ;;
  esac
}

edge() {
  "${UV[@]}" python "$ROOT/services/agent/edge_client.py" \
    --assume-role-arn "$TENANT_ROLE_ARN" --user-pool-id "$USER_POOL_ID" --client-id "$CLIENT_ID" "$@"
}

ask() {
  local who="$1" device="${DEVICE:-laptop-01}"
  shift
  edge ask "$@" --url "$INGRESS_URL" --user "$(user_email "$who")" \
    --cert "$STATE/devices/$device/device.pem" --key "$STATE/devices/$device/device.key"
}

case "${1:-help}" in
  setup)
    tw="$ROOT/terraform/tenant-workload"
    account=$(sed -n 's/^tenant_account_id *= *"\([0-9]*\)"/\1/p' "$tw/terraform.tfvars")
    out() { terraform -chdir="$tw" output -raw "$1"; }
    mkdir -p "$STATE" && umask 077
    {
      echo "INGRESS_URL=$(out ingress_url)"
      echo "USER_POOL_ID=$(out user_pool_id)"
      echo "CLIENT_ID=$(out jetson_app_client_id)"
      echo "HARNESS_ARN=$(out harness_arn)"
      echo "TENANT_ROLE_ARN=arn:aws:iam::$account:role/AWSControlTowerExecution"
      grep -h '^DEMO_USER_' "$ENV_FILE" 2>/dev/null || true
    } > "$ENV_FILE.new" && mv "$ENV_FILE.new" "$ENV_FILE"
    echo "Wrote $ENV_FILE. Add demo users as DEMO_USER_ALICE=<email> if missing."
    ;;
  make-user)  # make-user alice you+alice@example.com
    load
    edge make-user "$3"
    echo "DEMO_USER_$(echo "$2" | tr '[:lower:]' '[:upper:]')=$3" >> "$ENV_FILE"
    ;;
  warm)
    load
    ask alice "Reply with the single word: ready"
    ;;
  ask)
    load
    shift
    ask "$@"
    ;;
  revoked)  # a revoked device is refused before any model call
    load
    DEVICE=laptop-02 ask alice "hello" --data-tier local-only
    ;;
  local-only)
    load
    ask alice "Summarize this patient note." --data-tier local-only
    ;;
  no-cert)
    load
    curl -sS -m 15 -X POST "$INGRESS_URL" -d '{}' || echo "(TLS refused: no client certificate)"
    ;;
  no-token)
    load
    edge ask hello --url "$INGRESS_URL" --data-tier local-only \
      --cert "$STATE/devices/laptop-01/device.pem" --key "$STATE/devices/laptop-01/device.key"
    ;;
  audit)
    load
    "${UV[@]}" python - "$TENANT_ROLE_ARN" "${2:-5}" <<'EOF'
import boto3, json, sys, time
role, count = sys.argv[1], int(sys.argv[2])
creds = boto3.client("sts").assume_role(RoleArn=role, RoleSessionName="symphony-demo-audit")["Credentials"]
logs = boto3.client("logs", region_name="us-east-1", aws_access_key_id=creds["AccessKeyId"],
                    aws_secret_access_key=creds["SecretAccessKey"], aws_session_token=creds["SessionToken"])
pages = logs.get_paginator("filter_log_events").paginate(
    logGroupName="/symphony/demo/ingress", startTime=int((time.time() - 3600) * 1000), filterPattern='"symphony.ingress.audit"')
events = sorted((e for page in pages for e in page["events"]), key=lambda e: e["timestamp"])
for event in events[-count:]:
    print(json.dumps(json.loads(json.loads(event["message"])["message"])))
EOF
    ;;
  eval)
    load
    "${UV[@]}" python "$ROOT/services/agent/eval_harness.py" --harness-arn "$HARNESS_ARN" --assume-role-arn "$TENANT_ROLE_ARN"
    ;;
  *)
    sed -n '2,10p' "$0"
    ;;
esac
