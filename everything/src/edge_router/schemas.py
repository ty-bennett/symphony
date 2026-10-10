from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Complexity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    SENSITIVE = "SENSITIVE"


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    SENSITIVE = "SENSITIVE"
    UNKNOWN = "UNKNOWN"


class Route(StrEnum):
    LOCAL = "LOCAL"
    PRIVATE_AWS = "PRIVATE_AWS"
    FRONTIER = "FRONTIER"


class Message(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=1_000_000)


class SecurityContext(StrictModel):
    classification: Classification = Classification.UNKNOWN


class GenerationOptions(StrictModel):
    max_new_tokens: int = Field(default=128, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False)
    top_p: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)


class DecisionRequest(StrictModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    security_context: SecurityContext = Field(default_factory=SecurityContext)


class ChatRequest(DecisionRequest):
    generation: GenerationOptions | None = None
    stream: bool = False


class ComplexityPrediction(StrictModel):
    label: Complexity
    probabilities: dict[Complexity, float]
    confidence: float


class SensitivityPrediction(StrictModel):
    label: Sensitivity
    probabilities: dict[Sensitivity, float]
    confidence: float


class DecisionResult(StrictModel):
    request_id: str
    complexity: ComplexityPrediction
    sensitivity: SensitivityPrediction
    resolved_sensitivity: Sensitivity
    route: Route
    policy_reason: str
    security_flags: list[str]
    timing_ms: dict[str, float]
    prompt_tokens: int
    decision_model_version: str


class ChatResult(StrictModel):
    status: Literal["COMPLETED", "HANDOFF_REQUIRED"]
    route: Route
    request_id: str
    decision: DecisionResult
    text: str | None = None
    generated_tokens: int = 0
    finish_reason: str | None = None


class StreamEvent(StrictModel):
    event: Literal["decision", "token", "complete"]
    data: dict
