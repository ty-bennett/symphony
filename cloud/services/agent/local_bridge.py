"""Laptop stand-in for the Jetson gateway: an OpenAI-compatible endpoint for OpenCode.

OpenCode cannot present a device certificate, so this listens on localhost and
forwards /v1/chat/completions to Orchestra with mTLS and a test user's Cognito
token (see edge_client.py), streaming the answer back unchanged.

    uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/local_bridge.py \
        --assume-role-arn arn:aws:iam::<tenant>:role/AWSControlTowerExecution \
        --user-pool-id <pool> --client-id <jetson client> --user <test user> \
        --cert ~/.symphony/devices/laptop-01/device.pem --key ~/.symphony/devices/laptop-01/device.key

Then point an OpenAI-compatible client at http://127.0.0.1:4100/v1.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from edge_client import _session, access_token, post

MODELS = ["orchestra", "orchestra-step-up"]


def make_handler(
    upstream: str, cert: Path, key: Path, token: Any, tier: str
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/v1/models":
                data = [{"id": m, "object": "model", "owned_by": "symphony"} for m in MODELS]
                self._json(200, {"object": "list", "data": data})
            else:
                self._json(404, {"error": {"message": "not found", "code": "NOT_FOUND"}})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._json(404, {"error": {"message": "not found", "code": "NOT_FOUND"}})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            try:
                resp = post(upstream, body, token(), cert, key, headers={"X-Symphony-Data-Tier": tier})
            except Exception as exc:  # noqa: BLE001 - report any upstream failure to the client
                self._json(502, {"error": {"message": f"Orchestra unreachable: {exc}", "code": "UPSTREAM_UNAVAILABLE"}})
                return
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := resp.read1(65536):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.log_message("%s -> %d", self.path, resp.status)

        def log_message(self, fmt: str, *args: Any) -> None:
            # Status lines only; never prompts, answers, or tokens.
            sys.stderr.write(f"[bridge] {fmt % args}\n")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--assume-role-arn")
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--user", required=True, help="Test user in ~/.symphony/test-users")
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--url", default="https://api.demo.symphony-edge.link/v1/chat/completions")
    parser.add_argument("--data-tier", default="private-cloud",
                        choices=["private-cloud", "frontier-eligible"])
    parser.add_argument("--port", type=int, default=4100)
    args = parser.parse_args(argv)

    cognito = _session(args.assume_role_arn).client("cognito-idp", region_name=args.region)
    lock = threading.Lock()

    def token() -> str:
        with lock:  # one sign-in at a time: Cognito rejects a reused TOTP code
            return access_token(cognito, args.user_pool_id, args.client_id, args.user)

    token()  # fail fast on bad credentials
    handler = make_handler(args.url, args.cert.expanduser(), args.key.expanduser(), token, args.data_tier)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Orchestra bridge on http://127.0.0.1:{args.port}/v1 as {args.user} ({args.data_tier})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
