"""Two-destination policy. AWS hosts the cloud models; no separate frontier route."""

from pydantic import Field

from edge_router.config import read_yaml
from edge_router.routing.policy import PolicyDecision
from edge_router.schemas import (
    Classification,
    DifficultyPrediction,
    Route,
    SecurityContext,
    Sensitivity,
    StrictModel,
)


class DifficultyPolicyConfig(StrictModel):
    local_max_difficulty: float = Field(default=0.0, allow_inf_nan=False)
    # Deliberately not configurable to FRONTIER, even through a YAML change.
    cloud_route: Route = Route.PRIVATE_AWS

    @classmethod
    def load(cls, path: str):
        result = cls.model_validate(read_yaml(path))
        if result.cloud_route != Route.PRIVATE_AWS:
            raise ValueError("Difficulty routing supports only approved PRIVATE_AWS cloud")
        return result


class DifficultyRoutingPolicy:
    def __init__(self, config: DifficultyPolicyConfig):
        if config.cloud_route != Route.PRIVATE_AWS:
            raise ValueError("Cloud route must be PRIVATE_AWS, never FRONTIER")
        self.config = config

    def decide(
        self, difficulty: DifficultyPrediction, flags: list[str], context: SecurityContext
    ) -> PolicyDecision:
        # There is no sensitivity classifier in this artifact. UNKNOWN fails closed;
        # a regression score must never be interpreted as evidence of PUBLIC data.
        sensitive = bool(flags) or context.classification != Classification.PUBLIC
        resolved = Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC
        route = (
            Route.LOCAL
            if difficulty.score <= self.config.local_max_difficulty
            else Route.PRIVATE_AWS
        )
        reason = (
            f"Difficulty {difficulty.score:.4f} "
            f"{'<=' if route == Route.LOCAL else '>'} "
            f"local cutoff {self.config.local_max_difficulty:.4f}; "
            "cloud handoffs use approved AWS infrastructure only. "
        )
        if sensitive:
            reason += "Security overrides/unknown classification retained as SENSITIVE."
        else:
            reason += "Trusted PUBLIC metadata and no scanner flags."
        return PolicyDecision(route, resolved, reason)
