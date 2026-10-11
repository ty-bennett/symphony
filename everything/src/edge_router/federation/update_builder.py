import math
from pathlib import Path

import torch
from safetensors.torch import save_file

from edge_router.federation.models import FederatedUpdate
from edge_router.model.decision_heads import HeadSnapshot


def build_update(
    baseline: HeadSnapshot,
    trained,
    identity: dict,
    num_examples: int,
    counts: dict[str, int],
    max_norm: float,
    enabled: set[str],
    metrics: dict[str, float],
    proximal_mu: float = 0,
) -> FederatedUpdate:
    old = baseline.heads.state_dict()
    deltas = {
        key: (value.detach().cpu().float() - old[key].detach().cpu().float()).contiguous()
        for key, value in trained.state_dict().items()
        if key.split(".")[0] in enabled
    }
    norm = math.sqrt(sum(float(v.double().square().sum()) for v in deltas.values()))
    if not math.isfinite(norm):
        raise ValueError("Non-finite update rejected")
    clipped = norm > max_norm
    if clipped:
        # Leave a small rounding margin to guarantee the measured bound in FP32.
        scale = max_norm / norm * (1 - 1e-6)
        deltas = {k: v.mul(scale) for k, v in deltas.items()}
    final_norm = math.sqrt(sum(float(v.double().square().sum()) for v in deltas.values()))
    return FederatedUpdate(
        federation_schema_version=1,
        client_model_version=baseline.version,
        global_round=baseline.global_round,
        **{
            k: identity[k]
            for k in [
                "base_model_id",
                "base_model_revision",
                "tokenizer_hash",
                "chat_template_hash",
            ]
        },
        head_architecture_version=trained.architecture_version,
        num_examples=num_examples,
        head_example_counts=counts,
        deltas=deltas,
        aggregate_training_metrics=metrics,
        update_norm=final_norm,
        clipped=clipped,
        optimization="fedprox" if proximal_mu else "fedavg",
        backbone_quantization_hash=identity.get("backbone_quantization_hash"),
    )


def validate_update(update: FederatedUpdate, baseline: HeadSnapshot, identity: dict):
    if (
        update.federation_schema_version != 1
        or update.client_model_version != baseline.version
        or update.global_round != baseline.global_round
    ):
        raise ValueError("Update version mismatch")
    for key in ["base_model_id", "base_model_revision", "tokenizer_hash", "chat_template_hash"]:
        if getattr(update, key) != identity[key]:
            raise ValueError("Update backbone identity mismatch")
    if update.backbone_quantization_hash != identity.get("backbone_quantization_hash"):
        raise ValueError("Update quantized-backbone identity mismatch")
    if (
        update.head_architecture_version != baseline.heads.architecture_version
        or update.num_examples < 1
    ):
        raise ValueError("Invalid update architecture or example count")
    expected = baseline.heads.state_dict()
    if not update.deltas or set(update.head_example_counts) - {"complexity", "sensitivity"}:
        raise ValueError("Invalid head schema")
    for head in {key.split(".")[0] for key in update.deltas}:
        if head not in {"complexity", "sensitivity"} or {
            k for k in update.deltas if k.startswith(head + ".")
        } != {k for k in expected if k.startswith(head + ".")}:
            raise ValueError("Update must contain complete approved heads only")
        if not 0 < update.head_example_counts.get(head, 0) <= update.num_examples:
            raise ValueError("Invalid per-head example count")
    for key, value in update.deltas.items():
        if (
            key not in expected
            or value.shape != expected[key].shape
            or value.dtype != torch.float32
            or not torch.isfinite(value).all()
        ):
            raise ValueError("Invalid head delta")
    norm = math.sqrt(sum(float(v.double().square().sum()) for v in update.deltas.values()))
    if not math.isfinite(update.update_norm) or abs(norm - update.update_norm) > 1e-5:
        raise ValueError("Invalid update norm")
    approved_metrics = {
        "mean_training_loss",
        "training_steps",
        "feature_extraction_seconds",
        "head_training_seconds",
        "local_complexity_accuracy",
        "local_sensitivity_accuracy",
        "local_sensitivity_recall",
    }
    if set(update.aggregate_training_metrics) - approved_metrics or any(
        not isinstance(v, (float, int)) or not math.isfinite(v)
        for v in update.aggregate_training_metrics.values()
    ):
        raise ValueError("Outbound metrics must be finite aggregate scalars")
    if update.privacy_metadata != {"dp_enabled": False}:
        raise ValueError("DP privacy metadata requires a supported accountant")


def save_update(update: FederatedUpdate, directory: str | Path):
    import json

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=False)
    save_file(update.deltas, str(path / "head_deltas.safetensors"))
    (path / "update.json").write_text(json.dumps(update.metadata(), indent=2, allow_nan=False))
