from collections.abc import Iterator
from threading import RLock
from typing import Protocol

from edge_router.model.decision_heads import DecisionHeadManager, HeadSnapshot
from edge_router.model.shared_context import GeneratedToken, Instrumentation, PrefillContext
from edge_router.schemas import (
    ComplexityPrediction,
    DifficultyPrediction,
    GenerationOptions,
    Message,
    SensitivityPrediction,
)


class SharedBackboneBackend(Protocol):
    head_manager: DecisionHeadManager
    instrumentation: Instrumentation
    execution_lock: RLock
    identity: dict

    def prefill(self, messages: list[Message]) -> PrefillContext: ...

    def classify(
        self, context: PrefillContext, snapshot: HeadSnapshot
    ) -> tuple[ComplexityPrediction, SensitivityPrediction] | DifficultyPrediction: ...

    def decode_from_prefill(
        self, context: PrefillContext, options: GenerationOptions
    ) -> Iterator[GeneratedToken]: ...

    def model_info(self) -> dict: ...
