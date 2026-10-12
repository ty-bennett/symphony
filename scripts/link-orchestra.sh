#!/usr/bin/env bash
# One-time (and every 30 days): let the Jetson's gateway hand Soloist's cloud-bound
# prompts to Orchestra. From the laptop, with AWS access to the tenant:
#   - reuses the laptop-01 device certificate as the Jetson's (the cloud already trusts it),
#   - signs in a test user and stores their Cognito refresh token on the Jetson (0600),
#     where the gateway renews access tokens itself (public app client, no AWS keys there),
#   - points the gateway at both and restarts it.
#
#   scripts/link-orchestra.sh                       # as the demo user Alice
#   DEMO_USER=tybennett924+bob@gmail.com scripts/link-orchestra.sh
#   JETSON_HOST=10.35.x.x scripts/link-orchestra.sh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
JETSON_HOST=${JETSON_HOST:-10.35.237.33}
JETSON_USER=${JETSON_USER:-admin}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519}
DEVICE=${DEVICE:-$HOME/.symphony/devices/laptop-01}
export AWS_PROFILE=${AWS_PROFILE:-iamadmin-general}
set -a; source "$HOME/.symphony/demo.env"; set +a   # USER_POOL_ID, CLIENT_ID, TENANT_ROLE_ARN, DEMO_USER_ALICE
USER_NAME=${DEMO_USER:-$DEMO_USER_ALICE}

jetson() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=6 "$JETSON_USER@$JETSON_HOST" "$@"; }
jetson true || { echo "Can't reach the Jetson at $JETSON_HOST; set JETSON_HOST" >&2; exit 1; }
[[ -f $DEVICE/device.pem && -f $DEVICE/device.key ]] || { echo "No device certificate in $DEVICE" >&2; exit 1; }

echo "==> Signing in $USER_NAME"
# Prints "<sub> <refresh token>" on one line; nothing is written to disk here.
read -r -d '' SIGN_IN <<'EOF' || true
import base64, json, os, sys, time
from edge_client import USERS_DIR, _session, totp

username = sys.argv[1]
record = json.loads((USERS_DIR / f"{username}.json").read_text())
cognito = _session(os.environ["TENANT_ROLE_ARN"]).client("cognito-idp", region_name="us-east-1")
pool, client = os.environ["USER_POOL_ID"], os.environ["CLIENT_ID"]
result = cognito.admin_initiate_auth(
    UserPoolId=pool, ClientId=client, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": username, "PASSWORD": record["password"]},
)
if result.get("ChallengeName") == "SOFTWARE_TOKEN_MFA":
    # Cognito refuses a code it has already seen; wait for a fresh 30-second window.
    time.sleep(30 - time.time() % 30 + 1)
    result = cognito.admin_respond_to_auth_challenge(
        UserPoolId=pool, ClientId=client, ChallengeName="SOFTWARE_TOKEN_MFA", Session=result["Session"],
        ChallengeResponses={"USERNAME": username, "SOFTWARE_TOKEN_MFA_CODE": totp(record["totp_secret"])},
    )
elif result.get("ChallengeName"):
    sys.exit(f"Unsupported sign-in challenge {result['ChallengeName']}; run edge_client.py make-user first")
auth = result["AuthenticationResult"]
payload = auth["AccessToken"].split(".")[1]
sub = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
print(sub, auth["RefreshToken"])
EOF
SIGNED_IN=$(cd "$ROOT/cloud/services/agent" && uv run --no-project -q --with 'boto3>=1.43.111' python -c "$SIGN_IN" "$USER_NAME")
SUB=${SIGNED_IN%% *}
REFRESH=${SIGNED_IN#* }
[[ -n $SUB && -n $REFRESH && $SUB != "$REFRESH" ]] || { echo "Sign-in failed" >&2; exit 1; }
echo "Signed in (sub $SUB)"

echo "==> Installing the device certificate and refresh token on the Jetson"
jetson 'umask 077; mkdir -p ~/.symphony/device ~/.symphony/gateway/users && cat > ~/.symphony/device/device.pem' < "$DEVICE/device.pem"
jetson 'umask 077; cat > ~/.symphony/device/device.key' < "$DEVICE/device.key"
printf '%s\n' "$REFRESH" | jetson "umask 077; cat > ~/.symphony/gateway/users/$SUB.refresh"

echo "==> Pointing the gateway at Orchestra"
jetson "bash -s" <<REMOTE
set -euo pipefail
mkdir -p ~/.config/systemd/user/symphony-gateway.service.d
cat > ~/.config/systemd/user/symphony-gateway.service.d/orchestra.conf <<EOF
[Service]
Environment=SYMPHONY_DEVICE_CERT=\$HOME/.symphony/device/device.pem
Environment=SYMPHONY_DEVICE_KEY=\$HOME/.symphony/device/device.key
Environment=SYMPHONY_DEFAULT_USER_SUB=$SUB
Environment=SYMPHONY_COGNITO_CLIENT_ID=$CLIENT_ID
Environment=SYMPHONY_COGNITO_REGION=us-east-1
EOF
systemctl --user daemon-reload
systemctl --user restart symphony-gateway
sleep 1
systemctl --user is-active symphony-gateway
REMOTE
echo "Done. Cloud-bound prompts now run on Orchestra as $USER_NAME (refresh token good for 30 days)."
