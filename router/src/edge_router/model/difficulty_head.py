"""Load the already-trained difficulty-head artifact without retraining the LLM."""

import copy
import hashlib
import json
from pathlib import Path

import torch
from torch import nn

from edge_router.model.decision_heads import HeadSnapshot
from edge_router.schemas import DifficultyPrediction


class DifficultyHead(nn.Module):
    architecture_version = "standardized-relu-difficulty-v1"

    def __init__(self, hidden_size: int, layer: int, identity: dict, width: int = 256):
        super().__init__()
        self.hidden_size, self.layer, self.identity = hidden_size, layer, dict(identity)
        self.feature_size = hidden_size
        self.network = nn.Sequential(
            nn.Linear(hidden_size, width), nn.ReLU(), nn.Dropout(0.1), nn.Linear(width, 1)
        )
        self.register_buffer("mean", torch.zeros(hidden_size))
        self.register_buffer("std", torch.ones(hidden_size))

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        return self.network((hidden.float() - self.mean) / self.std)

    def isolated_copy(self, device=None):
        result = copy.deepcopy(self)
        return result.to(device) if device is not None else result

    def validate_for_serving(self):
        if (self.std <= 0).any() or self.layer != self.identity["L_d"]:
            raise ValueError("Invalid regression feature standardization/layer identity")


def load_difficulty_head(path: str | Path, device: str | torch.device = "cpu") -> HeadSnapshot:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Missing or oversized difficulty head artifact")
    version_path = path.with_name("version.json")
    if version_path.stat().st_size > 64 * 1024:
        raise ValueError("Oversized difficulty version metadata")
    metadata = json.loads(version_path.read_text())
    version = metadata.pop("version_id")
    expected = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()[:16]
    if version != expected:
        raise ValueError("Difficulty artifact version digest mismatch")
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if payload["version"] != metadata:
        raise ValueError("Head weights and version metadata disagree")
    hidden_size, layer = payload["d_model"], payload["layer"]
    if (
        not isinstance(hidden_size, int)
        or not 0 < hidden_size <= 16384
        or not isinstance(layer, int)
        or not 0 <= layer < 256
        or layer != metadata["L_d"]
        or not 0 < metadata["device_max_prompt_tokens"] <= 262144
    ):
        raise ValueError("Invalid difficulty architecture metadata")
    head = DifficultyHead(hidden_size, layer, metadata)
    head.network.load_state_dict(payload["state_dict"], strict=True)
    for name in ["mean", "std"]:
        value = payload[name]
        if value.shape != (hidden_size,) or not torch.isfinite(value).all():
            raise ValueError("Invalid feature standardization")
        getattr(head, name).copy_(value.float())
    if (head.std <= 0).any() or not all(
        torch.isfinite(t).all() for t in head.state_dict().values()
    ):
        raise ValueError("Non-finite head or invalid feature standard deviation")
    return HeadSnapshot(head.to(device).eval().requires_grad_(False), str(version))


def predict_difficulty(snapshot: HeadSnapshot, hidden: torch.Tensor) -> DifficultyPrediction:
    if not isinstance(snapshot.heads, DifficultyHead):
        raise TypeError("Qwen difficulty inference requires the approved regression head")
    with torch.inference_mode():
        output = snapshot.heads(hidden)
        if output.shape != (1, 1) or not torch.isfinite(output).all():
            raise RuntimeError("Invalid difficulty output; request cannot be routed")
        score = output.item()  # Only a scalar leaves the device, never the feature vector.
    return DifficultyPrediction(score=score, layer=snapshot.heads.layer)
