from typing import Protocol

from edge_router.federation.models import (
    FederatedMetrics,
    FederatedUpdate,
    GlobalModelPackage,
    SubmissionResult,
)


class FederationAdapter(Protocol):
    secure_aggregation_available: bool

    def get_global_model(self) -> GlobalModelPackage: ...
    def submit_update(self, update: FederatedUpdate) -> SubmissionResult: ...
    def submit_metrics(self, metrics: FederatedMetrics) -> None: ...


class NullFederationAdapter:
    secure_aggregation_available = False

    def get_global_model(self) -> GlobalModelPackage:
        raise ConnectionError("Federation adapter is disabled")

    def submit_update(self, update: FederatedUpdate) -> SubmissionResult:
        return SubmissionResult(False, "DISABLED")

    def submit_metrics(self, metrics: FederatedMetrics) -> None:
        pass
