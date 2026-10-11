"""Tenant-scoped, read-only S3 tools for an AgentCore Gateway Lambda target.

Gateway sends tool arguments as the event and tool metadata in
context.client_context.custom. Authorization depends on a narrowly scoped
Lambda invoke policy and customer-owned IAM role, not on model-provided values.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 32 * 1024
MAX_LIST_LIMIT = 20
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".log")
TOOL_NAMES = frozenset(("list_documents", "read_document"))


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Settings:
    bucket: str
    prefix: str
    role_arn: str
    external_id: str
    gateway_id: str
    target_id: str
    target_name: str

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        names = (
            "DOCS_BUCKET",
            "DOCS_PREFIX",
            "EXTERNAL_ROLE_ARN",
            "EXTERNAL_ID",
            "EXPECTED_GATEWAY_ID",
            "GATEWAY_TARGET_NAME",
        )
        missing = [name for name in names if not values.get(name)]
        if missing:
            raise ToolError("NOT_CONFIGURED", "Required tool configuration is missing")
        prefix = values["DOCS_PREFIX"]
        _relative_path(prefix.rstrip("/"), allow_empty=False)
        if not values["EXTERNAL_ROLE_ARN"].startswith("arn:aws:iam::"):
            raise ToolError("NOT_CONFIGURED", "External role ARN is invalid")
        target_name = values["GATEWAY_TARGET_NAME"]
        if "___" in target_name:
            raise ToolError("NOT_CONFIGURED", "Gateway target name is invalid")
        return cls(
            bucket=values["DOCS_BUCKET"],
            prefix=prefix.rstrip("/") + "/",
            role_arn=values["EXTERNAL_ROLE_ARN"],
            external_id=values["EXTERNAL_ID"],
            gateway_id=values["EXPECTED_GATEWAY_ID"],
            # Optional: the target ID only exists after the target is registered,
            # which itself depends on this function. Gateway ID, tool prefix, and
            # the IAM invoke boundary still apply when it is unset.
            target_id=values.get("EXPECTED_TARGET_ID", ""),
            target_name=target_name,
        )


def _relative_path(value: Any, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ToolError(
            "INVALID_ARGUMENT", "Expected a relative path of at most 1024 characters"
        )
    if not value and allow_empty:
        return value
    if not value or value.startswith("/") or "\\" in value:
        raise ToolError(
            "INVALID_ARGUMENT", "Path must be relative to the approved folder"
        )
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ToolError("INVALID_ARGUMENT", "Path contains an invalid segment")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ToolError("INVALID_ARGUMENT", "Path contains a control character")
    return value


def _tool_from_context(context: Any, settings: Settings) -> str:
    try:
        metadata = context.client_context.custom
    except AttributeError as exc:
        raise ToolError("UNAUTHORIZED", "Gateway metadata is required") from exc
    if not isinstance(metadata, dict):
        raise ToolError("UNAUTHORIZED", "Gateway metadata is required")
    if metadata.get("bedrockAgentCoreGatewayId") != settings.gateway_id or (
        settings.target_id
        and metadata.get("bedrockAgentCoreTargetId") != settings.target_id
    ):
        raise ToolError("UNAUTHORIZED", "Gateway target is not approved")
    full_name = metadata.get("bedrockAgentCoreToolName")
    prefix = settings.target_name + "___"
    if not isinstance(full_name, str) or not full_name.startswith(prefix):
        raise ToolError("UNAUTHORIZED", "Tool target is not approved")
    tool_name = full_name[len(prefix) :]
    if tool_name not in TOOL_NAMES:
        raise ToolError("UNKNOWN_TOOL", "Tool is not available")
    return tool_name


def _session_name(context: Any) -> str:
    request_id = getattr(context, "aws_request_id", "")
    safe_id = re.sub(r"[^A-Za-z0-9-]", "", str(request_id))[:44]
    return "symphony-" + (safe_id or "tool-call")


def _assumed_s3(settings: Settings, context: Any, sts_client: Any) -> Any:
    import boto3

    response = sts_client.assume_role(
        RoleArn=settings.role_arn,
        RoleSessionName=_session_name(context),
        ExternalId=settings.external_id,
        DurationSeconds=900,
    )
    credentials = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    ).client("s3")


def _list_documents(
    event: dict[str, Any], settings: Settings, s3: Any
) -> dict[str, Any]:
    if set(event) - {"relative_prefix", "limit"}:
        raise ToolError("INVALID_ARGUMENT", "Unsupported argument")
    relative_prefix = _relative_path(event.get("relative_prefix", ""), allow_empty=True)
    limit = event.get("limit", 10)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_LIST_LIMIT
    ):
        raise ToolError("INVALID_ARGUMENT", "limit must be an integer from 1 to 20")
    search_prefix = settings.prefix + (relative_prefix + "/" if relative_prefix else "")
    response = s3.list_objects_v2(
        Bucket=settings.bucket, Prefix=search_prefix, MaxKeys=limit + 1
    )
    objects = [
        {"key": item["Key"][len(settings.prefix) :], "size_bytes": item["Size"]}
        for item in response.get("Contents", [])
        if item["Key"].startswith(search_prefix) and item["Key"] != search_prefix
    ]
    return {
        "ok": True,
        "documents": objects[:limit],
        "truncated": bool(response.get("IsTruncated")) or len(objects) > limit,
    }


def _read_document(
    event: dict[str, Any], settings: Settings, s3: Any
) -> dict[str, Any]:
    if set(event) != {"key"}:
        raise ToolError("INVALID_ARGUMENT", "Exactly one document key is required")
    key = _relative_path(event["key"], allow_empty=False)
    if not key.lower().endswith(TEXT_SUFFIXES):
        raise ToolError(
            "UNSUPPORTED_DOCUMENT", "Only approved text document types can be read"
        )
    response = s3.get_object(Bucket=settings.bucket, Key=settings.prefix + key)
    body = response["Body"]
    try:
        raw = body.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        body.close()
    if b"\x00" in raw:
        raise ToolError("UNSUPPORTED_DOCUMENT", "Document is not UTF-8 text")
    truncated = len(raw) > MAX_DOCUMENT_BYTES
    try:
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        text = decoder.decode(raw[:MAX_DOCUMENT_BYTES], final=not truncated)
    except UnicodeDecodeError as exc:
        raise ToolError("UNSUPPORTED_DOCUMENT", "Document is not UTF-8 text") from exc
    return {"ok": True, "key": key, "text": text, "truncated": truncated}


def run_tool(
    event: Any,
    context: Any,
    *,
    settings: Settings,
    s3: Any | None = None,
    sts_client: Any | None = None,
    s3_factory: Callable[[Settings, Any, Any], Any] = _assumed_s3,
) -> dict[str, Any]:
    tool_name = _tool_from_context(context, settings)
    if not isinstance(event, dict):
        raise ToolError("INVALID_ARGUMENT", "Tool arguments must be an object")
    # Validate before STS so malformed or denied calls never touch customer resources.
    if tool_name == "list_documents":
        if set(event) - {"relative_prefix", "limit"}:
            raise ToolError("INVALID_ARGUMENT", "Unsupported argument")
        _relative_path(event.get("relative_prefix", ""), allow_empty=True)
        limit = event.get("limit", 10)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIST_LIMIT
        ):
            raise ToolError("INVALID_ARGUMENT", "limit must be an integer from 1 to 20")
    else:
        if set(event) != {"key"}:
            raise ToolError("INVALID_ARGUMENT", "Exactly one document key is required")
        key = _relative_path(event["key"], allow_empty=False)
        if not key.lower().endswith(TEXT_SUFFIXES):
            raise ToolError(
                "UNSUPPORTED_DOCUMENT", "Only approved text document types can be read"
            )
    if s3 is None:
        if sts_client is None:
            import boto3

            sts_client = boto3.client("sts")
        s3 = s3_factory(settings, context, sts_client)
    if tool_name == "list_documents":
        return _list_documents(event, settings, s3)
    return _read_document(event, settings, s3)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    """Return JSON for Gateway without exposing AWS error details or document content to logs."""
    try:
        result = run_tool(event, context, settings=Settings.from_env())
        LOGGER.info("customer document tool succeeded")
        return result
    except ToolError as exc:
        LOGGER.warning("customer document tool rejected: %s", exc.code)
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:
        LOGGER.error("customer document tool failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "error": {
                "code": "TOOL_UNAVAILABLE",
                "message": "Document tool is unavailable",
            },
        }
