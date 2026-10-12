from datetime import datetime, timezone
from enum import StrEnum

from pydantic import Field

from edge_router.schemas import Complexity, DecisionRequest, Sensitivity, StrictModel


class LabelSource(StrEnum):
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    TRUSTED_SECURITY_METADATA = "TRUSTED_SECURITY_METADATA"
    ADMINISTRATOR = "ADMINISTRATOR"
    ROUTE_OUTCOME = "ROUTE_OUTCOME"
    AUTOMATED_HEURISTIC = "AUTOMATED_HEURISTIC"


TRUSTED_SENSITIVITY_SOURCES = frozenset(
    {
        LabelSource.HUMAN_CONFIRMED,
        LabelSource.TRUSTED_SECURITY_METADATA,
        LabelSource.ADMINISTRATOR,
    }
)


class LocalExample(DecisionRequest):
    complexity: Complexity | None = None
    sensitivity: Sensitivity | None = None
    label_source: LabelSource = LabelSource.ADMINISTRATOR
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome_metadata: dict[str, str] | None = None
    route: str | None = None


class FeedbackRecord(StrictModel):
    request_id: str
    corrected_complexity: Complexity | None = None
    corrected_sensitivity: Sensitivity | None = None
    label_source: LabelSource
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
