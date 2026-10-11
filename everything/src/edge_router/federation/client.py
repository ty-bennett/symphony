from datetime import datetime, timezone
from time import perf_counter

from edge_router.federation.adapter import FederationAdapter
from edge_router.federation.checkpoint_manager import FederatedCheckpointManager
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FederatedMetrics, FederationConfig
from edge_router.feedback.models import LocalExample
from edge_router.observability.logging import log_event


class FederatedClient:
    """Invoke in a background worker. Failures never touch the request-time path."""

    def __init__(
        self,
        adapter: FederationAdapter,
        trainer: FederatedLocalTrainer,
        checkpoints: FederatedCheckpointManager,
        config: FederationConfig,
    ):
        self.adapter, self.trainer, self.checkpoints, self.config = (
            adapter,
            trainer,
            checkpoints,
            config,
        )
        self.status = "IDLE"
        self.last_attempt: str | None = None
        self.last_successful_round: int | None = None

    def run_round(self, examples: list[LocalExample]) -> bool:
        if not self.config.enabled:
            self.status = "DISABLED"
            return False
        self.last_attempt = datetime.now(timezone.utc).isoformat()
        start = perf_counter()
        try:
            if (
                self.config.secure_aggregation_required
                and not self.adapter.secure_aggregation_available
            ):
                self.status = "SECURE_AGGREGATION_UNAVAILABLE"
                return False
            if self.config.production and not self.checkpoints.production:
                self.status = "PRODUCTION_VERIFICATION_REQUIRED"
                return False
            package = self.adapter.get_global_model()
            from edge_router.federation.checkpoint_manager import read_package

            approved = read_package(package, self.checkpoints.identity, self.checkpoints.verifier)[
                0
            ]
            current = self.checkpoints.heads.snapshot()
            if approved.global_round < current.global_round:
                raise ValueError("Stale global package")
            if (
                approved.version == current.version
                and approved.global_round == current.global_round
            ):
                # Same approved baseline: compare parameters to prevent same-version equivocation.
                import torch

                if any(
                    not torch.equal(v.cpu(), current.heads.state_dict()[k].cpu())
                    for k, v in approved.heads.state_dict().items()
                ):
                    raise ValueError("Global package changed without a version advance")
            else:
                if not self.checkpoints.activate(package):
                    self.status = "GLOBAL_MODEL_REJECTED"
                    return False
            # Train a copy of the approved global snapshot, never the manager's live modules.
            approved.heads.to(next(current.heads.parameters()).device)
            result = self.trainer.train(approved, examples)
            submission = self.adapter.submit_update(result.update)
            if not submission.accepted:
                self.status = "SUBMISSION_REJECTED"
                return False
            metrics = FederatedMetrics(
                approved.global_round,
                self.checkpoints.heads.snapshot().version,
                {
                    "local_training_example_count": float(result.update.num_examples),
                    "local_training_duration": perf_counter() - start,
                    "update_norm": result.update.update_norm,
                    "update_clipped": float(result.update.clipped),
                },
            )
            self.adapter.submit_metrics(metrics)
            self.last_successful_round = approved.global_round
            self.status = "UPDATE_SUBMITTED"
            return True
        except Exception:
            self.status = "FEDERATION_ATTEMPT_FAILED"
            log_event("federation_failure", status=self.status)
            return False

    def info(self) -> dict:
        active = self.checkpoints.heads.snapshot()
        return {
            "current_global_round": active.global_round,
            "active_decision_model_version": active.version,
            "last_federation_attempt": self.last_attempt,
            "last_successful_federation_round": self.last_successful_round,
            "status": self.status,
            "candidate_validation_status": self.checkpoints.last_status,
        }
