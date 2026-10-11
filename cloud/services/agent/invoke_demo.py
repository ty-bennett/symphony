"""Send one prompt to the tenant's AgentCore Harness and stream the answer.

Operator demo client for the private-cloud path (Phase 3 agent slice). It signs
with SigV4; the Jetson/mTLS ingress replaces it in a later plan.

    uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/invoke_demo.py \
        --harness-arn "$(terraform -chdir=cloud/terraform/tenant-workload output -raw harness_arn)" \
        --assume-role-arn arn:aws:iam::<tenant-account>:role/AWSControlTowerExecution \
        "What does our PTO policy say?"
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Iterable
from typing import Any, TextIO


def new_session_id() -> str:
    """InvokeHarness requires a runtimeSessionId of at least 33 characters."""
    return f"symphony-demo-{uuid.uuid4()}"


def render_events(events: Iterable[dict[str, Any]], out: TextIO) -> dict[str, Any]:
    """Print streamed text and tool calls; return a summary for the caller."""
    summary: dict[str, Any] = {
        "stop_reason": None,
        "tools": [],
        "usage": None,
        "errors": [],
    }
    for event in events:
        if "contentBlockStart" in event:
            tool = event["contentBlockStart"].get("start", {}).get("toolUse")
            if tool:
                summary["tools"].append(tool["name"])
                out.write(f"\n[tool] {tool['name']}\n")
        elif "contentBlockDelta" in event:
            text = event["contentBlockDelta"].get("delta", {}).get("text")
            if text:
                out.write(text)
        elif "messageStop" in event:
            summary["stop_reason"] = event["messageStop"].get("stopReason")
        elif "metadata" in event:
            summary["usage"] = event["metadata"].get("usage")
        elif "runtimeClientError" in event:
            message = event["runtimeClientError"].get("message", "unknown error")
            summary["errors"].append(message)
            out.write(f"\n[error] {message}\n")
    out.write("\n")
    return summary


def harness_client(region: str, assume_role_arn: str | None) -> Any:
    import boto3

    session = boto3.Session()
    if assume_role_arn:
        credentials = session.client("sts").assume_role(
            RoleArn=assume_role_arn, RoleSessionName="symphony-agent-demo"
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
    return session.client("bedrock-agentcore", region_name=region)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt")
    parser.add_argument("--harness-arn", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--assume-role-arn")
    parser.add_argument(
        "--session-id", default=None, help="Reuse to continue a conversation"
    )
    args = parser.parse_args(argv)

    client = harness_client(args.region, args.assume_role_arn)
    response = client.invoke_harness(
        harnessArn=args.harness_arn,
        runtimeSessionId=args.session_id or new_session_id(),
        messages=[{"role": "user", "content": [{"text": args.prompt}]}],
    )
    summary = render_events(response["stream"], sys.stdout)
    print(
        f"[stop] {summary['stop_reason']}  [tools] {summary['tools']}  [usage] {summary['usage']}"
    )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
