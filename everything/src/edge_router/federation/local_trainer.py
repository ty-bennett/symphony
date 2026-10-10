from dataclasses import dataclass
from time import perf_counter

import torch
from torch import nn

from edge_router.federation.feature_extractor import (
    FrozenFeatureExtractor,
    LocalFeature,
    feature_identity,
)
from edge_router.federation.models import FeatureMode, FederatedUpdate, FederationConfig
from edge_router.federation.update_builder import build_update, validate_update
from edge_router.feedback.models import LocalExample
from edge_router.model.decision_heads import HeadSnapshot
from edge_router.model.memory import synchronize


@dataclass
class TrainingResult:
    candidate: HeadSnapshot
    update: FederatedUpdate
    feature_extraction_seconds: float
    head_training_seconds: float


class FederatedLocalTrainer:
    """Optimizes isolated FP32 heads. Never references backbone parameters in an optimizer."""

    def __init__(self, extractor: FrozenFeatureExtractor, config: FederationConfig):
        self.extractor, self.config = extractor, config

    def train(
        self,
        approved: HeadSnapshot,
        examples: list[LocalExample] | None = None,
        features: list[LocalFeature] | None = None,
    ) -> TrainingResult:
        config = self.config
        config.privacy.ensure_supported()
        backend = self.extractor.backend
        # Explicitly assert the freeze rather than repairing an unsafe setup silently.
        if any(p.requires_grad for p in backend.backbone.parameters()) or any(
            p.requires_grad for p in backend.lm_head.parameters()
        ):
            raise ValueError("Head-only training requires every backbone and LM parameter frozen")
        device = next(approved.heads.parameters()).device
        heads = approved.heads.isolated_copy(device)
        enabled = set()
        if config.federate_complexity:
            enabled.add("complexity")
        if config.federate_sensitivity:
            enabled.add("sensitivity")
        for name, parameter in heads.named_parameters():
            parameter.requires_grad_(name.split(".")[0] in enabled)
        heads.train()
        examples = (examples or [])[: config.max_examples_per_round]
        feature_seconds = 0.0
        if features is not None and config.feature_mode == FeatureMode.ONLINE_FEATURE_EXTRACTION:
            raise ValueError("Online mode requires local examples")
        if features is not None:
            features = features[: config.max_examples_per_round]
        elif config.feature_mode == FeatureMode.PRECOMPUTED_FEATURES:
            synchronize()
            start = perf_counter()
            features = [self.extractor.extract(example) for example in examples]
            synchronize()
            feature_seconds += perf_counter() - start
        size = len(features) if features is not None else len(examples)
        if size < config.minimum_examples_per_round:
            raise ValueError("Insufficient local training data")
        params = [p for p in heads.parameters() if p.requires_grad]
        if config.optimizer == "adamw":
            optimizer = torch.optim.AdamW(params, lr=config.learning_rate)
        elif config.optimizer == "sgd":
            optimizer = torch.optim.SGD(params, lr=config.learning_rate)
        else:
            raise ValueError("Optimizer must be adamw or sgd")
        weights = {
            "complexity": torch.tensor(
                config.complexity_class_weights, device=device, dtype=torch.float32
            ),
            "sensitivity": torch.tensor(
                config.sensitivity_class_weights, device=device, dtype=torch.float32
            ),
        }
        original = {k: v.detach().clone().to(device) for k, v in approved.heads.named_parameters()}
        generator = torch.Generator().manual_seed(config.random_seed)
        dropout_generator = torch.Generator(device=device).manual_seed(config.random_seed)
        counts = {head: 0 for head in enabled}
        loss_sum, steps, head_seconds = 0.0, 0, 0.0
        evaluation_features: dict[int, LocalFeature] = {}
        for epoch in range(config.local_epochs):
            order = torch.randperm(size, generator=generator).tolist()
            for offset in range(0, size, config.batch_size):
                indices = order[offset : offset + config.batch_size]
                if features is None:
                    synchronize()
                    start = perf_counter()
                    batch = [self.extractor.extract(examples[i]) for i in indices]
                    synchronize()
                    feature_seconds += perf_counter() - start
                else:
                    batch = [features[i] for i in indices]
                if any(
                    x.backbone_identity_hash != feature_identity(backend.identity) for x in batch
                ):
                    raise ValueError("Precomputed feature backbone identity mismatch")
                if epoch == config.local_epochs - 1:
                    evaluation_features.update(zip(indices, batch, strict=True))
                start = perf_counter()
                vectors = torch.stack([x.vector.detach().clone() for x in batch]).to(
                    device=device, dtype=torch.float32
                )
                if (
                    vectors.shape != (len(batch), heads.feature_size)
                    or not torch.isfinite(vectors).all()
                ):
                    raise ValueError("Invalid local feature shape or values")
                c, s = heads(vectors, generator=dropout_generator)
                loss = torch.zeros((), device=device)
                for head, logits, labels, weight in [
                    (
                        "complexity",
                        c,
                        [x.complexity_index for x in batch],
                        config.complexity_loss_weight,
                    ),
                    (
                        "sensitivity",
                        s,
                        [x.sensitivity_index for x in batch],
                        config.sensitivity_loss_weight,
                    ),
                ]:
                    if head not in enabled:
                        continue
                    target = torch.tensor(labels, device=device)
                    valid = target >= 0
                    if valid.any():
                        loss = loss + weight * nn.functional.cross_entropy(
                            logits[valid], target[valid], weight=weights[head]
                        )
                        if epoch == 0:
                            counts[head] += int(valid.sum())
                if not loss.requires_grad:
                    continue
                if config.proximal_mu:
                    penalty = sum(
                        (p - original[k]).square().sum()
                        for k, p in heads.named_parameters()
                        if p.requires_grad
                    )
                    loss = loss + config.proximal_mu / 2 * penalty
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(params, config.gradient_clip_norm, error_if_nonfinite=True)
                optimizer.step()
                loss_sum += float(loss.detach())
                steps += 1
                head_seconds += perf_counter() - start
        eligible = {head for head in enabled if counts[head] > 0}
        if not steps or not eligible:
            raise ValueError("No eligible training labels for enabled heads")
        # Unlabelled heads must not export optimizer weight-decay changes.
        counts = {head: counts[head] for head in eligible}
        metrics = {
            "mean_training_loss": loss_sum / steps,
            "training_steps": float(steps),
            "feature_extraction_seconds": feature_seconds,
            "head_training_seconds": head_seconds,
        }
        heads.eval()
        correct = {"complexity": 0, "sensitivity": 0}
        totals = {"complexity": 0, "sensitivity": 0}
        sensitive_total, sensitive_correct = 0, 0
        with torch.no_grad():
            local_features = list(evaluation_features.values())
            for offset in range(0, len(local_features), config.batch_size):
                batch = local_features[offset : offset + config.batch_size]
                c, s = heads(torch.stack([f.vector for f in batch]).to(device))
                for i, feature in enumerate(batch):
                    for head, logits, target in [
                        ("complexity", c, feature.complexity_index),
                        ("sensitivity", s, feature.sensitivity_index),
                    ]:
                        if target >= 0:
                            totals[head] += 1
                            correct[head] += int(logits[i].argmax() == target)
                    if feature.sensitivity_index == 1:
                        sensitive_total += 1
                        sensitive_correct += int(s[i].argmax() == 1)
        for head in enabled:
            if totals[head]:
                metrics[f"local_{head}_accuracy"] = correct[head] / totals[head]
        if "sensitivity" in enabled and sensitive_total:
            metrics["local_sensitivity_recall"] = sensitive_correct / sensitive_total
        update = build_update(
            approved,
            heads,
            backend.identity,
            size,
            counts,
            config.max_update_norm,
            eligible,
            metrics,
            config.proximal_mu,
        )
        validate_update(update, approved, backend.identity)
        heads.eval().requires_grad_(False)
        candidate = HeadSnapshot(
            heads,
            f"local-from-{approved.version}",
            approved.complexity_temperature,
            approved.sensitivity_temperature,
            approved.global_round,
        )
        return TrainingResult(candidate, update, feature_seconds, head_seconds)
