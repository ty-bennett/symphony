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
import base64
import hashlib
import hmac
import http.client
import json
import secrets
import ssl
import struct
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


def totp(secret: str, *, at: float | None = None, step: int = 30) -> str:
    """RFC 6238 six-digit code (what an authenticator app shows)."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    ) % 1_000_000
    return f"{code:06d}"


def access_token(
    cognito: Any,
    pool_id: str,
    client_id: str,
    username: str,
    *,
    users_dir: Path = USERS_DIR,
    now: float | None = None,
) -> str:
    """Sign in a test user; the pool requires TOTP MFA, enrolled on first sign-in.

    Tokens are cached until a minute before expiry: Cognito rejects a reused TOTP code.
    """
    now = time.time() if now is None else now
    path = users_dir / f"{username}.json"
    record = json.loads(path.read_text())
    if record.get("access_token") and record.get("expires_at", 0) - 60 > now:
        return record["access_token"]
    result = cognito.admin_initiate_auth(
        UserPoolId=pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": record["password"]},
    )
    challenge = result.get("ChallengeName")
    if challenge == "MFA_SETUP":
        secret = cognito.associate_software_token(Session=result["Session"])
        record["totp_secret"] = secret["SecretCode"]
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        verified = cognito.verify_software_token(
            Session=secret["Session"], UserCode=totp(record["totp_secret"])
        )
        result = cognito.admin_respond_to_auth_challenge(
            UserPoolId=pool_id,
            ClientId=client_id,
            ChallengeName="MFA_SETUP",
            Session=verified["Session"],
            ChallengeResponses={"USERNAME": username},
        )
    elif challenge == "SOFTWARE_TOKEN_MFA":
        result = cognito.admin_respond_to_auth_challenge(
            UserPoolId=pool_id,
            ClientId=client_id,
            ChallengeName="SOFTWARE_TOKEN_MFA",
            Session=result["Session"],
            ChallengeResponses={
                "USERNAME": username,
                "SOFTWARE_TOKEN_MFA_CODE": totp(record["totp_secret"]),
            },
        )
    elif challenge:
        raise RuntimeError(f"unsupported sign-in challenge {challenge}")
    auth = result["AuthenticationResult"]
    record.update(access_token=auth["AccessToken"], expires_at=now + auth["ExpiresIn"])
    path.write_text(json.dumps(record))
    path.chmod(0o600)
    return auth["AccessToken"]


def post(
    url: str,
    body: str,
    token: str | None,
    cert: Path,
    key: Path,
    headers: dict[str, str] | None = None,
) -> http.client.HTTPResponse:
    parts = urlsplit(url)
    context = ssl.create_default_context()
    context.load_cert_chain(cert, key)
    conn = http.client.HTTPSConnection(
        parts.hostname, parts.port or 443, context=context, timeout=310
    )
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **(headers or {})}
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
