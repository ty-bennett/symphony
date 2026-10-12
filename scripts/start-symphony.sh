#!/usr/bin/env bash
# Bring Symphony up after a power-on, from the laptop:
#   Jetson: Soloist (edge router, 127.0.0.1:8000 on the box) and symphony-gateway (:8088),
#           both user services that start at boot from now on.
#   Laptop: opencode 1.18.35 (127.0.0.1:4096) pointed at the gateway, and the app on
#           http://localhost:3000 (sign-in off, this laptop only).
#
#   scripts/start-symphony.sh [folder for the coding agent]     # default ~/symphony-workspace
#   JETSON_HOST=10.35.x.x scripts/start-symphony.sh             # if the Jetson's address changed
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP=$ROOT/app
WORKDIR=${1:-$HOME/symphony-workspace}
JETSON_USER=${JETSON_USER:-admin}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519}
LOGS=$APP/.symphony/logs
mkdir -p "$LOGS" "$WORKDIR"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jetson() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new "$JETSON_USER@$JETSON_HOST" "$@"; }
wait_for() { # name, seconds, command...
  local name=$1 seconds=$2; shift 2
  for ((i = 0; i < seconds; i += 2)); do "$@" >/dev/null 2>&1 && return 0; sleep 2; done
  die "$name did not come up within ${seconds}s (logs in $LOGS)"
}
free_port() { # stop whatever this laptop runs on the port
  local pids; pids=$(lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true)
  [[ -n $pids ]] && kill $pids 2>/dev/null && sleep 1
  return 0
}

say "Finding the Jetson"
for host in ${JETSON_HOST:-} 10.35.237.33 192.168.55.1; do
  JETSON_HOST=$host
  jetson true 2>/dev/null && break
  JETSON_HOST=
done
[[ -n $JETSON_HOST ]] || die "Can't reach the Jetson over SSH. Put the laptop on the Jetson's network (or plug in USB), then run: JETSON_HOST=<its ip> $0"
echo "Jetson at $JETSON_HOST"

say "Making sure the Jetson runs this gateway build"
BIN=$ROOT/gateway/dist/symphony-gateway-linux-arm64
if command -v go >/dev/null; then
  (cd "$ROOT/gateway" && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o "$BIN" .)
fi
[[ -f $BIN ]] || die "No gateway build at $BIN (install Go, or build it elsewhere)"
if [[ $(shasum -a 256 "$BIN" | cut -d' ' -f1) != $(jetson 'sha256sum ~/symphony/symphony-gateway 2>/dev/null | cut -d" " -f1') ]]; then
  scp -q -i "$SSH_KEY" -o BatchMode=yes "$BIN" "$JETSON_USER@$JETSON_HOST:symphony/symphony-gateway.new"
  jetson 'cd ~/symphony && { [ -f symphony-gateway ] && cp -p symphony-gateway symphony-gateway.bak || true; } && chmod +x symphony-gateway.new && mv symphony-gateway.new symphony-gateway'
  echo "Updated (previous build kept as symphony-gateway.bak)"
else
  echo "Already current"
fi

say "Starting Soloist and the gateway on the Jetson (first model load takes a few minutes)"
jetson 'bash -s' <<'REMOTE'
set -euo pipefail
units=~/.config/systemd/user
mkdir -p "$units/symphony-gateway.service.d"
# The router folder was renamed from everything/ to router/; boxes set up before keep the old name.
router=router; [ -d ~/symphony/router ] || router=everything
cat > "$units/soloist.service" <<EOF
[Unit]
Description=Soloist: Symphony's edge router and local model (127.0.0.1:8000)
After=network-online.target

[Service]
WorkingDirectory=%h/symphony/$router
Environment=PYTHONPATH=src
ExecStart=%h/symphony/.venv-qwen-awq/bin/python -m edge_router.api.server --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
cat > "$units/symphony-gateway.service.d/soloist.conf" <<'EOF'
[Unit]
Wants=soloist.service
After=soloist.service

[Service]
Environment=SYMPHONY_GATEWAY_LISTEN=0.0.0.0:8088
Environment=SOLOIST_ROUTER_URL=http://127.0.0.1:8000
Environment=SOLOIST_MODEL=qwen3.5-2b
EOF
# A Soloist started by hand (nohup) would hold port 8000; let the service own it.
if ! systemctl --user is-active --quiet soloist; then pkill -f 'edge_router.api.server' || true; sleep 2; fi
systemctl --user daemon-reload
systemctl --user enable --now soloist symphony-gateway >/dev/null 2>&1
systemctl --user restart symphony-gateway
for i in $(seq 1 300); do
  curl -s -m 2 127.0.0.1:8000/health | grep -q '"model_loaded":true' && break
  [ "$i" = 300 ] && { echo "Soloist did not load; see: journalctl --user -u soloist -n 50"; exit 1; }
  sleep 2
done
echo "Soloist ready"
REMOTE

GATEWAY=http://$JETSON_HOST:8088
wait_for "The gateway at $GATEWAY" 60 curl -sf -m 3 "$GATEWAY/healthz"
KEY=$(jetson 'cat ~/.symphony/gateway/local.key' | tr -d '\n')
[[ -n $KEY ]] || die "No gateway key on the Jetson (~/.symphony/gateway/local.key)"
echo "Gateway ready at $GATEWAY"

if [[ ${SKIP_CHECK:-} != 1 ]]; then
  say "Checking routing: one prompt Soloist keeps, one it hands to Orchestra"
  for prompt in "What is 2+2?" "Design a multi-region active-active database architecture with conflict resolution and write a migration plan."; do
    body=$(python3 -c 'import json,sys; print(json.dumps({"model":"auto","max_tokens":48,"messages":[{"role":"user","content":sys.argv[1]}]}))' "$prompt")
    curl -s -m 300 "$GATEWAY/v1/chat/completions" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d "$body" |
      python3 -c 'import json,sys
d = json.load(sys.stdin); p = sys.argv[1][:40].ljust(42)
if "error" in d: print("  " + p + " -> error: " + str(d["error"].get("message")))
else: print("  " + p + " -> " + str(d.get("model")) + ": " + repr(d["choices"][0]["message"]["content"].strip()[:60]))' "$prompt" || echo "  $prompt -> no answer"
  done
  echo "  (Orchestra errors mean it isn't linked yet: run scripts/link-orchestra.sh)"
fi

say "Pointing opencode at the gateway"
mkdir -p "$APP/.symphony/opencode"
CONFIG=$APP/.symphony/opencode/opencode.json
python3 - "$CONFIG" "$GATEWAY/v1" "$KEY" <<'EOF'
import json, sys
path, url, key = sys.argv[1:]
json.dump({
    "$schema": "https://opencode.ai/config.json",
    "model": "symphony/auto",
    "provider": {"symphony": {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Symphony",
        "options": {"baseURL": url, "apiKey": key},
        "models": {"auto": {"name": "Symphony (Soloist routes)", "tool_call": True}},
    }},
}, open(path, "w"), indent=2)
EOF
chmod 600 "$CONFIG"

say "Starting opencode and the app on this laptop"
[[ -f $APP/.env.local ]] || printf '# Local demo only: no sign-in, everyone is the owner. Served on 127.0.0.1 only.\nOC_ALLOW_UNAUTHENTICATED=1\nOPENCODE_URL=http://127.0.0.1:4096\n' > "$APP/.env.local"
[[ -d $APP/node_modules ]] || (cd "$APP" && npm install)
free_port 4096
free_port 3000
(cd "$APP" && OPENCODE_CONFIG=$CONFIG nohup npm run opencode -- "$WORKDIR" > "$LOGS/opencode.log" 2>&1 &)
(cd "$APP" && nohup npx next dev -H 127.0.0.1 -p 3000 > "$LOGS/app.log" 2>&1 &)
wait_for "opencode" 60 curl -sf -m 2 http://127.0.0.1:4096/global/health
wait_for "The app" 120 curl -sf -m 10 http://127.0.0.1:3000/api/oc/health

say "Symphony is up"
cat <<EOF
  App:          http://localhost:3000   (Coding agent tab, model "Symphony (Soloist routes)")
  Agent folder: $WORKDIR
  Gateway:      $GATEWAY  ->  Soloist decides each prompt
  Logs:         $LOGS
  Stop laptop side:  lsof -t -iTCP:3000 -iTCP:4096 -sTCP:LISTEN | xargs kill
EOF
command -v open >/dev/null && open http://localhost:3000 || true
