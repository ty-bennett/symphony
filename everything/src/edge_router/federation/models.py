from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import torch
from pydantic import Field, model_validator

from edge_router.federation.privacy import PrivacyConfig
from edge_router.schemas import Complexity, Sensitivity, StrictModel


class FeatureMode(StrEnum):
    ONLINE_FEATURE_EXTRACTION = "ONLINE_FEATURE_EXTRACTION"
    PRECOMPUTED_FEATURES = "PRECOMPUTED_FEATURES"


class GateConfig(StrictModel):
    minimum_complexity_accuracy: float = Field(default=0.70, ge=0, le=1)
    minimum_sensitivity_recall: float = Field(default=0.98, ge=0, le=1)
    maximum_sensitive_false_negative_rate: float = Field(default=0.02, ge=0, le=1)
    minimum_examples: int = Field(default=2, ge=2)


class FederationConfig(StrictModel):
    enabled: bool = False
    adapter: str = "simulation"
    production: bool = False
    secure_aggregation_required: bool = True
    federate_complexity: bool = True
    federate_sensitivity: bool = True
    feature_mode: FeatureMode = FeatureMode.PRECOMPUTED_FEATURES
    local_epochs: int = Field(default=2, ge=1)
    learning_rate: float = Field(default=0.001, gt=0)
    batch_size: int = Field(default=16, ge=1)
    optimizer: str = "adamw"
    max_examples_per_round: int = Field(default=1000, ge=1)
    minimum_examples_per_round: int = Field(default=2, ge=1)
    complexity_loss_weight: float = Field(default=1, gt=0)
    sensitivity_loss_weight: float = Field(default=1, gt=0)
    complexity_class_weights: list[float] = Field(default_factory=lambda: [1, 1, 1])
    sensitivity_class_weights: list[float] = Field(default_factory=lambda: [1, 2])
    gradient_clip_norm: float = Field(default=1, gt=0)
    max_update_norm: float = Field(default=1, gt=0)
    proximal_mu: float = Field(default=0, ge=0)
    random_seed: int = 42
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    validation: GateConfig = Field(default_factory=GateConfig)

    @model_validator(mode="after")
    def check(self):
        if not self.federate_complexity and not self.federate_sensitivity:
            raise ValueError("Enable at least one head")
        for weights, length in [
            (self.complexity_class_weights, 3),
            (self.sensitivity_class_weights, 2),
        ]:
            if len(weights) != length or any(not 0 < x < float("inf") for x in weights):
                raise ValueError("Class weights must be finite positive values with correct length")
        if self.minimum_examples_per_round > self.max_examples_per_round:
            raise ValueError("Minimum examples exceeds round limit")
        return self


class PackageMetadata(StrictModel):
    federation_schema_version: int = 1
    global_round: int = Field(default=0, ge=0)
    model_version: str
    base_model_id: str
    base_model_revision: str
    tokenizer_hash: str
    chat_template_hash: str
    hidden_size: int = Field(gt=0)
    complexity_label_schema: list[str] = Field(default_factory=lambda: list(Complexity))
    sensitivity_label_schema: list[str] = Field(default_factory=lambda: list(Sensitivity))
    head_architecture_version: str = "mlp-layernorm-silu-v1"
    head_width: int = Field(default=256, gt=0, le=4096)
    head_dropout: float = Field(default=0.1, ge=0, lt=1)
    backbone_adapter_hash: str | None = None
    decision_prompt_hash: str | None = None
    backbone_quantization_hash: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class GlobalModelPackage:
    directory: Path


@dataclass(slots=True)
class FederatedUpdate:
    """Closed outbound schema: only head deltas and aggregate scalars are permitted."""

    federation_schema_version: int
    client_model_version: str
    global_round: int
    base_model_id: str
    base_model_revision: str
    tokenizer_hash: str
    chat_template_hash: str
    head_architecture_version: str
    num_examples: int
    head_example_counts: dict[str, int]
    deltas: dict[str, torch.Tensor]
    aggregate_training_metrics: dict[str, float]
    update_norm: float
    clipped: bool
    optimization: str = "fedavg"
    privacy_metadata: dict[str, float | bool] = field(default_factory=lambda: {"dp_enabled": False})
    backbone_quantization_hash: str | None = None

    def metadata(self) -> dict:
        return {
            item.name: getattr(self, item.name) for item in fields(self) if item.name != "deltas"
        }


@dataclass(frozen=True)
class FederatedMetrics:
    global_round: int
    active_model_version: str
    aggregates: dict[str, float]


@dataclass(frozen=True)
class SubmissionResult:
    accepted: bool
    status: str
