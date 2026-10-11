"""Tenant-scoped semantic search over the customer's approved documents.

AgentCore Gateway Lambda target exposing one tool, search_documents. The
knowledge base ID is deployment configuration; the model supplies only the
query text and a bounded result count. Returned passages are untrusted data.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_QUERY_CHARS = 1000
MAX_RESULTS = 5
MAX_PASSAGE_CHARS = 2000
TOOL_NAME = "search_documents"


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Settings:
    knowledge_base_id: str
    gateway_id: str
    target_name: str
    source_prefix: str

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        names = (
            "KNOWLEDGE_BASE_ID",
            "EXPECTED_GATEWAY_ID",
            "GATEWAY_TARGET_NAME",
            "SOURCE_PREFIX",
        )
        if any(not values.get(name) for name in names):
            raise ToolError("NOT_CONFIGURED", "Required tool configuration is missing")
        if "___" in values["GATEWAY_TARGET_NAME"]:
            raise ToolError("NOT_CONFIGURED", "Gateway target name is invalid")
        return cls(
            knowledge_base_id=values["KNOWLEDGE_BASE_ID"],
            gateway_id=values["EXPECTED_GATEWAY_ID"],
            target_name=values["GATEWAY_TARGET_NAME"],
            source_prefix=values["SOURCE_PREFIX"],
        )


def _check_gateway(context: Any, settings: Settings) -> None:
    try:
        metadata = context.client_context.custom
    except AttributeError as exc:
        raise ToolError("UNAUTHORIZED", "Gateway metadata is required") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("bedrockAgentCoreGatewayId") != settings.gateway_id
    ):
        raise ToolError("UNAUTHORIZED", "Gateway target is not approved")
    if (
        metadata.get("bedrockAgentCoreToolName")
        != f"{settings.target_name}___{TOOL_NAME}"
    ):
        raise ToolError("UNKNOWN_TOOL", "Tool is not available")


def _validate(event: Any) -> tuple[str, int]:
    if not isinstance(event, dict) or set(event) - {"query", "max_results"}:
        raise ToolError("INVALID_ARGUMENT", "Only query and max_results are accepted")
    query = event.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ToolError("INVALID_ARGUMENT", "query must be 1 to 1000 characters")
    max_results = event.get("max_results", 3)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_RESULTS
    ):
        raise ToolError(
            "INVALID_ARGUMENT", "max_results must be an integer from 1 to 5"
        )
    return query.strip(), max_results


def _relative_source(uri: str, settings: Settings) -> str:
    marker = "/" + settings.source_prefix
    index = uri.find(marker)
    return uri[index + len(marker) :] if index >= 0 else "unknown"


def search(
    event: Any, context: Any, *, settings: Settings, client: Any | None = None
) -> dict[str, Any]:
    _check_gateway(context, settings)
    query, max_results = _validate(event)
    if client is None:
        import boto3

        client = boto3.client("bedrock-agent-runtime")
    response = client.retrieve(
        knowledgeBaseId=settings.knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": max_results}
        },
    )
    results = []
    for item in response.get("retrievalResults", [])[:max_results]:
        text = item.get("content", {}).get("text", "")
        uri = item.get("location", {}).get("s3Location", {}).get("uri", "")
        results.append(
            {
                "source": _relative_source(uri, settings),
                "text": text[:MAX_PASSAGE_CHARS],
                "truncated": len(text) > MAX_PASSAGE_CHARS,
                "score": item.get("score"),
            }
        )
    return {"ok": True, "results": results}


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    """Return JSON for Gateway without logging queries or passages."""
    try:
        result = search(event, context, settings=Settings.from_env())
        LOGGER.info("knowledge search succeeded")
        return result
    except ToolError as exc:
        LOGGER.warning("knowledge search rejected: %s", exc.code)
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:  # noqa: BLE001 - never leak AWS error detail to the model
        LOGGER.error("knowledge search failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "error": {"code": "TOOL_UNAVAILABLE", "message": "Search is unavailable"},
        }
