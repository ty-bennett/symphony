"""Tenant operations that have no Terraform resource yet.

    uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/tenant_ops.py \
        --assume-role-arn arn:aws:iam::<tenant>:role/AWSControlTowerExecution <command> [...]

Commands:
  zdr                 Set Bedrock data retention to "none" and verify it (us-east-1, us-east-2, us-west-2).
  kb-sync KB DS       Start a knowledge base ingestion job and wait for it to finish.
  lockdown-logs KEY   Give AgentCore runtime log groups 7-day retention and the tenant KMS key.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any

ZDR_REGIONS = ("us-east-1", "us-east-2", "us-west-2")
RUNTIME_LOG_PREFIX = "/aws/bedrock-agentcore/runtimes/"
LOG_RETENTION_DAYS = 7


def ensure_zero_retention(
    client_for: Callable[[str], Any], regions=ZDR_REGIONS
) -> dict[str, str]:
    """Set mode none where needed; return the verified mode per Region."""
    verified = {}
    for region in regions:
        client = client_for(region)
        if client.get_account_data_retention().get("mode") != "none":
            client.put_account_data_retention(mode="none")
        verified[region] = client.get_account_data_retention().get("mode")
    failures = {region: mode for region, mode in verified.items() if mode != "none"}
    if failures:
        raise RuntimeError(f"data retention is not none in: {failures}")
    return verified


def sync_knowledge_base(
    client: Any,
    kb_id: str,
    ds_id: str,
    *,
    poll_seconds: float = 10,
    timeout_seconds: float = 900,
    sleep=time.sleep,
) -> dict[str, Any]:
    job = client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)[
        "ingestionJob"
    ]
    waited = 0.0
    while job["status"] in ("STARTING", "IN_PROGRESS"):
        if waited >= timeout_seconds:
            raise RuntimeError("ingestion job timed out")
        sleep(poll_seconds)
        waited += poll_seconds
        job = client.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job["ingestionJobId"],
        )["ingestionJob"]
    if job["status"] != "COMPLETE":
        raise RuntimeError(
            f"ingestion job {job['status']}: {job.get('failureReasons', [])}"
        )
    return job.get("statistics", {})


def lockdown_runtime_logs(client: Any, kms_key_arn: str) -> list[str]:
    changed = []
    paginator = client.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix=RUNTIME_LOG_PREFIX):
        for group in page.get("logGroups", []):
            name = group["logGroupName"]
            if group.get("retentionInDays") != LOG_RETENTION_DAYS:
                client.put_retention_policy(
                    logGroupName=name, retentionInDays=LOG_RETENTION_DAYS
                )
            if group.get("kmsKeyId") != kms_key_arn:
                client.associate_kms_key(logGroupName=name, kmsKeyId=kms_key_arn)
            changed.append(name)
    return changed


def _session(assume_role_arn: str | None) -> Any:
    import boto3

    session = boto3.Session()
    if not assume_role_arn:
        return session
    creds = session.client("sts").assume_role(
        RoleArn=assume_role_arn, RoleSessionName="symphony-tenant-ops"
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Symphony tenant operations")
    parser.add_argument("--assume-role-arn")
    parser.add_argument("--region", default="us-east-1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("zdr")
    kb = sub.add_parser("kb-sync")
    kb.add_argument("knowledge_base_id")
    kb.add_argument("data_source_id")
    logs = sub.add_parser("lockdown-logs")
    logs.add_argument("kms_key_arn")
    args = parser.parse_args(argv)
    session = _session(args.assume_role_arn)
    if args.command == "zdr":
        print(
            ensure_zero_retention(
                lambda region: session.client("bedrock", region_name=region)
            )
        )
    elif args.command == "kb-sync":
        print(
            sync_knowledge_base(
                session.client("bedrock-agent", region_name=args.region),
                args.knowledge_base_id,
                args.data_source_id,
            )
        )
    else:
        print(
            lockdown_runtime_logs(
                session.client("logs", region_name=args.region), args.kms_key_arn
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
