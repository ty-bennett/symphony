"""Run the harness evaluation set and print a pass/fail table.

Run before any system prompt or model change; the suite passes at its threshold.

    uv run --no-project --with 'boto3>=1.43.111' python cloud/services/agent/eval_harness.py \
        --harness-arn "$(terraform -chdir=cloud/terraform/tenant-workload output -raw harness_arn)" \
        --assume-role-arn arn:aws:iam::<tenant-account>:role/AWSControlTowerExecution
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import invoke_demo

SUITE = Path(__file__).resolve().parent / "evals" / "harness_v1.json"


def grade(
    case: dict[str, Any], answer: str, tools: list[str], errors: list[str]
) -> dict[str, Any]:
    text = answer.lower()
    reasons = []
    wanted = case.get("tools_any")
    if wanted and not any(w in t for t in tools for w in wanted):
        reasons.append(f"expected a tool matching {wanted}")
    for tool in tools:
        if any(f in tool for f in case.get("forbid_tools", [])):
            reasons.append(f"used forbidden tool {tool}")
    for phrase in case.get("include_all", []):
        if phrase.lower() not in text:
            reasons.append(f"missing {phrase!r}")
    any_of = case.get("include_any")
    if any_of and not any(p.lower() in text for p in any_of):
        reasons.append(f"missing any of {any_of}")
    for phrase in case.get("exclude", []):
        if phrase.lower() in text:
            reasons.append(f"must not contain {phrase!r}")
    reasons += [f"stream error: {e}" for e in errors]
    return {
        "id": case["id"],
        "status": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "tools": tools,
    }


def table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| case | result | tools | seconds | reasons |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        tools = ", ".join(dict.fromkeys(r["tools"])) or "-"
        reasons = "; ".join(r["reasons"]) or "-"
        lines.append(
            f"| {r['id']} | {r['status']} | {tools} | {r['seconds']:.1f} | {reasons} |"
        )
    passed = sum(r["status"] == "PASS" for r in rows)
    lines.append(f"\n{passed}/{len(rows)} passed")
    return "\n".join(lines)


def run_case(
    client: Any, harness_arn: str, case: dict[str, Any], model_id: str | None
) -> dict[str, Any]:
    started = time.monotonic()
    request = invoke_demo.invoke_request(
        harness_arn,
        invoke_demo.new_session_id(),
        case["prompt"],
        user="eval-runner",
        model_id=model_id,
    )
    out = io.StringIO()
    try:
        response = client.invoke_harness(**request)
        summary = invoke_demo.render_events(response["stream"], out)
    except Exception as error:  # a failed call is a failed case, not a crashed suite
        summary = {"tools": [], "errors": [f"{type(error).__name__}: {error}"]}
    row = grade(case, out.getvalue(), summary["tools"], summary["errors"])
    row["seconds"] = time.monotonic() - started
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness-arn", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--assume-role-arn")
    parser.add_argument("--model-id")
    parser.add_argument("--only", action="append", help="Run only these case IDs")
    parser.add_argument("--suite", type=Path, default=SUITE)
    args = parser.parse_args(argv)

    suite = json.loads(args.suite.read_text())
    cases = [c for c in suite["cases"] if not args.only or c["id"] in args.only]
    client = invoke_demo.harness_client(args.region, args.assume_role_arn)
    rows = []
    for case in cases:
        rows.append(run_case(client, args.harness_arn, case, args.model_id))
        print(f"{rows[-1]['id']}: {rows[-1]['status']}", file=sys.stderr)
    print(table(rows))
    passed = sum(r["status"] == "PASS" for r in rows)
    return 0 if passed >= min(suite["pass_threshold"], len(rows)) else 1


if __name__ == "__main__":
    sys.exit(main())
