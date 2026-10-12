from collections.abc import Iterator
from typing import Protocol

from edge_router.model.backend import SharedBackboneBackend
from edge_router.model.shared_context import GeneratedToken, PrefillContext
from edge_router.schemas import ChatResult, DecisionRequest, DecisionResult, GenerationOptions


class RemoteExecutor(Protocol):
    def execute(self, request: DecisionRequest, decision: DecisionResult) -> ChatResult: ...


class HandoffExecutor:
    """No network, no remote credentials, no implicit content transmission."""

    def execute(self, request: DecisionRequest, decision: DecisionResult) -> ChatResult:
        return ChatResult(
            status="HANDOFF_REQUIRED",
            route=decision.route,
            request_id=decision.request_id,
            decision=decision,
        )


class LocalExecutor:
    def __init__(self, backend: SharedBackboneBackend):
        self.backend = backend

    def execute(
        self, context: PrefillContext, options: GenerationOptions
    ) -> Iterator[GeneratedToken]:
        return self.backend.decode_from_prefill(context, options)
