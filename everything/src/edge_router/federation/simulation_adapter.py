"""Development coordinator only. No networking or secure-aggregation claim."""

from edge_router.federation.adapter import FederationAdapter
from edge_router.federation.checkpoint_manager import save_checkpoint
from edge_router.federation.models import (
    FederatedMetrics,
    FederatedUpdate,
    GlobalModelPackage,
    SubmissionResult,
)
from edge_router.federation.update_builder import validate_update
from edge_router.model.decision_heads import HeadSnapshot


class LocalSimulationFederationAdapter(FederationAdapter):
    secure_aggregation_available = False

    def __init__(self, global_package: GlobalModelPackage, baseline: HeadSnapshot, identity: dict):
        self.package, self.baseline, self.identity = global_package, baseline, identity
        self.updates: list[FederatedUpdate] = []
        self.metrics: list[FederatedMetrics] = []

    def get_global_model(self) -> GlobalModelPackage:
        return self.package

    def submit_update(self, update: FederatedUpdate) -> SubmissionResult:
        validate_update(update, self.baseline, self.identity)
        self.updates.append(update)
        return SubmissionResult(True, "SIMULATION_ACCEPTED")

    def submit_metrics(self, metrics: FederatedMetrics) -> None:
        self.metrics.append(metrics)

    def aggregate(self, directory) -> GlobalModelPackage:
        if not self.updates:
            raise ValueError("No compatible client updates")
        heads = self.baseline.heads.isolated_copy("cpu")
        state = heads.state_dict()
        for key in state:
            head = key.split(".")[0]
            updates = [u for u in self.updates if key in u.deltas]
            if updates:
                total = sum(u.head_example_counts[head] for u in updates)
                delta = sum(u.deltas[key] * (u.head_example_counts[head] / total) for u in updates)
                state[key] = state[key] + delta
        heads.load_state_dict(state)
        next_round = self.baseline.global_round + 1
        snapshot = HeadSnapshot(
            heads,
            f"global-round-{next_round}",
            self.baseline.complexity_temperature,
            self.baseline.sensitivity_temperature,
            next_round,
        )
        self.package = save_checkpoint(snapshot, self.identity, directory)
        self.baseline = snapshot
        self.updates.clear()
        return self.package
