"""Laptop stand-in for the Jetson client: mTLS + Cognito token + SSE stream.

Used to verify the ingress live; the real client is built by the on-prem team
from cloud/schemas. Test users' passwords stay in ~/.symphony/test-users/.

    uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/edge_client.py \
        --assume-role-arn arn:aws:iam::<tenant>:role/AWSControlTowerExecution \
        --user-pool-id <pool> --client-id <jetson client> \
        make-user alice
    ... ask --url https://api.demo.symphony-edge.link/v1/requests \
        --cert device.pem --key device.key --user alice "What is our PTO policy?"
"""

from __future__ import annotations

import argparse
import http.client
import json
import secrets
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

USERS_DIR = Path.home() / ".symphony" / "test-users"


def parse_sse(lines: Iterable[bytes]) -> Iterator[tuple[str, Any]]:
    event, data = None, []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if event and data:
                yield event, json.loads("\n".join(data))
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if event and data:
        yield event, json.loads("\n".join(data))


def envelope(
    prompt: str, conversation_id: str, data_tier: str, model_preference: str
) -> str:
    return json.dumps(
        {
            "version": 1,
            "request_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "data_tier": data_tier,
            "model_preference": model_preference,
            "messages": [{"role": "user", "text": prompt}],
        }
    )


def summarize(
    events: Iterable[tuple[str, Any]],
    *,
    clock: Callable[[], float] = time.monotonic,
    started: float,
    out=None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"answer": "", "tools": [], "ttft_s": None, "end": None}
    for name, data in events:
        if name == "meta":
            summary["meta"] = data
        elif name == "tool":
            summary["tools"].append(data["name"])
            if out:
                out.write(f"\n[tool] {data['name']}\n")
        elif name == "token":
            if summary["ttft_s"] is None:
                summary["ttft_s"] = round(clock() - started, 3)
            summary["answer"] += data["text"]
            if out:
                out.write(data["text"])
                out.flush()
        elif name in ("done", "error"):
            summary["end"] = name
            summary[name] = data
    summary["total_s"] = round(clock() - started, 3)
    return summary


def _session(assume_role_arn: str | None) -> Any:
    import boto3

    session = boto3.Session()
    if assume_role_arn:
        creds = session.client("sts").assume_role(
            RoleArn=assume_role_arn, RoleSessionName="symphony-edge-client"
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return session


def make_user(cognito: Any, pool_id: str, username: str) -> dict[str, str]:
    password = secrets.token_urlsafe(18) + "aA1!"
    cognito.admin_create_user(
        UserPoolId=pool_id, Username=username, MessageAction="SUPPRESS"
    )
    cognito.admin_set_user_password(
        UserPoolId=pool_id, Username=username, Password=password, Permanent=True
    )
    USERS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = USERS_DIR / f"{username}.json"
    path.write_text(json.dumps({"username": username, "password": password}))
    path.chmod(0o600)
    user = cognito.admin_get_user(UserPoolId=pool_id, Username=username)
    return {a["Name"]: a["Value"] for a in user["UserAttributes"]}


def access_token(cognito: Any, pool_id: str, client_id: str, username: str) -> str:
    password = json.loads((USERS_DIR / f"{username}.json").read_text())["password"]
    result = cognito.admin_initiate_auth(
        UserPoolId=pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return result["AuthenticationResult"]["AccessToken"]


def post(
    url: str, body: str, token: str | None, cert: Path, key: Path
) -> http.client.HTTPResponse:
    parts = urlsplit(url)
    context = ssl.create_default_context()
    context.load_cert_chain(cert, key)
    conn = http.client.HTTPSConnection(
        parts.hostname, parts.port or 443, context=context, timeout=310
    )
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", parts.path, body=body.encode(), headers=headers)
    return conn.getresponse()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--assume-role-arn")
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--client-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("make-user").add_argument("username")
    ask = sub.add_parser("ask")
    ask.add_argument("prompt")
    ask.add_argument("--url", required=True)
    ask.add_argument("--cert", type=Path, required=True)
    ask.add_argument("--key", type=Path, required=True)
    ask.add_argument("--user", help="Test user; omit to send no token")
    ask.add_argument("--token", help="Send this token instead of signing in")
    ask.add_argument("--conversation-id", default=f"conv-{uuid.uuid4()}")
    ask.add_argument("--body", help="Send this raw JSON body instead of an envelope")
    ask.add_argument("--data-tier", default="private-cloud")
    ask.add_argument("--model", default="default", choices=["default", "step-up"])
    args = parser.parse_args(argv)

    cognito = _session(args.assume_role_arn).client(
        "cognito-idp", region_name=args.region
    )
    if args.command == "make-user":
        attrs = make_user(cognito, args.user_pool_id, args.username)
        print(f"created {args.username} sub={attrs.get('sub')}")
        return 0

    token = args.token
    if token is None and args.user:
        token = access_token(cognito, args.user_pool_id, args.client_id, args.user)
    body = args.body or envelope(
        args.prompt, args.conversation_id, args.data_tier, args.model
    )
    started = time.monotonic()
    response = post(args.url, body, token, args.cert, args.key)
    print(f"[status] {response.status} {response.getheader('Content-Type')}")
    if "text/event-stream" not in (response.getheader("Content-Type") or ""):
        print(response.read().decode())
        return 1
    summary = summarize(parse_sse(response), started=started, out=sys.stdout)
    print(f"\n[end] {summary['end']} {summary.get(summary['end'] or '', '')}")
    print(f"[meta] {summary.get('meta')}  [tools] {summary['tools']}")
    print(f"[timing] first token {summary['ttft_s']}s  total {summary['total_s']}s")
    return 0 if summary["end"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
