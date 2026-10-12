from dataclasses import dataclass

import torch

from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.federation.models import GateConfig
from edge_router.feedback.models import LocalExample
from edge_router.model.decision_heads import HeadSnapshot
from edge_router.routing.policy import RoutingPolicy
from edge_router.schemas import Sensitivity
from training.evaluation import evaluate_heads


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reason: str
    metrics: dict


class FederatedModelGate:
    def __init__(
        self,
        extractor: FrozenFeatureExtractor,
        protected_examples: list[LocalExample],
        policy: RoutingPolicy,
        config: GateConfig,
    ):
        self.extractor, self.examples, self.policy, self.config = (
            extractor,
            [example.model_copy(deep=True) for example in protected_examples],
            policy,
            config,
        )

    def evaluate(self, candidate: HeadSnapshot) -> GateResult:
        try:
            if len(self.examples) < self.config.minimum_examples:
                return GateResult(False, "INSUFFICIENT_VALIDATION_DATA", {})
            if not all(torch.isfinite(p).all() for p in candidate.heads.parameters()):
                return GateResult(False, "NONFINITE_PARAMETERS", {})
            if candidate.heads.hidden_size != self.extractor.backend.identity["hidden_size"]:
                return GateResult(False, "INCOMPATIBLE_HIDDEN_SIZE", {})
            if (
                candidate.heads.architecture_version
                != self.extractor.backend.head_manager.snapshot().heads.architecture_version
            ):
                return GateResult(False, "INCOMPATIBLE_HEAD_ARCHITECTURE", {})
            candidate.heads.eval().requires_grad_(False)
            with torch.inference_mode():
                device = next(candidate.heads.parameters()).device
                outputs = candidate.heads(
                    torch.zeros((1, candidate.heads.feature_size), device=device)
                )
                if (
                    outputs[0].shape != (1, 3)
                    or outputs[1].shape != (1, 2)
                    or not all(torch.isfinite(x).all() for x in outputs)
                ):
                    return GateResult(False, "INVALID_OUTPUT", {})
            metrics = evaluate_heads(candidate, self.extractor, self.examples, self.policy)
            c, s, r = metrics["complexity"], metrics["sensitivity"], metrics["routing"]
            if (
                c["examples"] == 0
                or s["confusion_matrix"][0] == [0, 0]
                or s["confusion_matrix"][1] == [0, 0]
            ):
                return GateResult(False, "MISSING_PROTECTED_CLASSES", metrics)
            passed = (
                c["accuracy"] >= self.config.minimum_complexity_accuracy
                and s["recall"][Sensitivity.SENSITIVE] >= self.config.minimum_sensitivity_recall
                and s["sensitive_false_negative_rate"]
                <= self.config.maximum_sensitive_false_negative_rate
                and r["sensitive_examples_allowed_frontier"] == 0
                and r["deterministic_invariant_violations"] == 0
            )
            return GateResult(passed, "ACCEPTED" if passed else "QUALITY_GATE_FAILED", metrics)
        except Exception:
            return GateResult(False, "VALIDATION_ERROR", {})
