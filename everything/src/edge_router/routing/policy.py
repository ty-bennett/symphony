import math
import os
from dataclasses import dataclass

from pydantic import Field, model_validator

from edge_router.config import read_yaml
from edge_router.schemas import (
    Classification,
    Complexity,
    ComplexityPrediction,
    Route,
    SecurityContext,
    Sensitivity,
    SensitivityPrediction,
    StrictModel,
)


class PolicyConfig(StrictModel):
    public_confidence_required_for_frontier: float = Field(default=0.95, gt=0.5, le=1)
    sensitivity_confidence_threshold: float = Field(default=0.80, gt=0.5, le=1)
    uncertain_route: Route = Route.PRIVATE_AWS
    matrix: dict[Sensitivity, dict[Complexity, Route]]

    @model_validator(mode="after")
    def validate_matrix(self):
        if self.uncertain_route == Route.FRONTIER:
            raise ValueError("Uncertainty cannot fall back to FRONTIER")
        if set(self.matrix) != set(Sensitivity):
            raise ValueError("Policy must include both sensitivity classes")
        for sensitivity, row in self.matrix.items():
            if set(row) != set(Complexity):
                raise ValueError("Policy must include all complexity classes")
            if sensitivity == Sensitivity.SENSITIVE and Route.FRONTIER in row.values():
                raise ValueError("SENSITIVE -> FRONTIER is forbidden")
        return self

    @classmethod
    def load(cls, path: str, use_env: bool = True):
        data = read_yaml(path)
        if use_env:
            for env, key in [
                ("PUBLIC_CONFIDENCE_FOR_FRONTIER", "public_confidence_required_for_frontier"),
                ("SENSITIVITY_THRESHOLD", "sensitivity_confidence_threshold"),
            ]:
                if env in os.environ:
                    data[key] = float(os.environ[env])
        return cls.model_validate(data)


@dataclass(frozen=True)
class PolicyDecision:
    route: Route
    resolved_sensitivity: Sensitivity
    reason: str


class RoutingPolicy:
    """FRONTIER is an allow-list destination. PUBLIC metadata never downgrades risk."""

    def __init__(self, config: PolicyConfig):
        self.config = config

    def decide(
        self,
        complexity: ComplexityPrediction,
        sensitivity: SensitivityPrediction,
        flags: list[str],
        context: SecurityContext,
    ) -> PolicyDecision:
        probabilities = [
            sensitivity.probabilities.get(label, float("nan")) for label in Sensitivity
        ]
        valid = (
            all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities)
            and abs(sum(probabilities) - 1) < 1e-4
            and math.isfinite(sensitivity.confidence)
            and abs(sensitivity.confidence - sensitivity.probabilities.get(sensitivity.label, -1))
            < 1e-4
            and sensitivity.confidence >= max(probabilities) - 1e-4
        )
        uncertain = (
            not valid or sensitivity.confidence < self.config.sensitivity_confidence_threshold
        )
        reasons = []
        if context.classification == Classification.SENSITIVE:
            reasons.append("trusted_sensitive_metadata")
        if flags:
            reasons.append("deterministic_security_detection")
        if sensitivity.label == Sensitivity.SENSITIVE:
            reasons.append("learned_sensitive_prediction")
        if uncertain:
            reasons.append("uncertain_sensitivity_fail_closed")
        resolved = Sensitivity.SENSITIVE if reasons else Sensitivity.PUBLIC
        route = self.config.matrix[resolved][complexity.label]
        if uncertain:
            route = self.config.uncertain_route
        frontier_allowed = (
            valid
            and not uncertain
            and resolved == Sensitivity.PUBLIC
            and not flags
            and context.classification != Classification.SENSITIVE
            and sensitivity.probabilities[Sensitivity.PUBLIC]
            >= self.config.public_confidence_required_for_frontier
        )
        if route == Route.FRONTIER and not frontier_allowed:
            route = self.config.uncertain_route
            reasons.append("frontier_public_confidence_not_met")
        # This guard is deliberately independent of the configurable matrix.
        if resolved == Sensitivity.SENSITIVE and route == Route.FRONTIER:
            route = Route.PRIVATE_AWS
        return PolicyDecision(route, resolved, ";".join(reasons) or "public_complexity_matrix")
