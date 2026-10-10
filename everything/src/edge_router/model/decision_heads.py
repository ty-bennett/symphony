import copy
import threading
from dataclasses import dataclass

import torch
from torch import nn

from edge_router.schemas import (
    Complexity,
    ComplexityPrediction,
    Sensitivity,
    SensitivityPrediction,
)


class DecisionHeads(nn.Module):
    architecture_version = "mlp-layernorm-silu-v1"

    @property
    def feature_size(self) -> int:
        return self.hidden_size

    def __init__(self, hidden_size: int, width: int = 256, dropout: float = 0.1):
        super().__init__()
        self.hidden_size, self.width, self.dropout = hidden_size, width, dropout
        self.complexity = self._head(3)
        self.sensitivity = self._head(2)

    def _head(self, classes: int):
        return nn.Sequential(
            nn.LayerNorm(self.hidden_size),
            nn.Linear(self.hidden_size, self.width),
            nn.SiLU(),
            nn.Dropout(self.dropout),
            nn.Linear(self.width, classes),
        )

    def forward(
        self, hidden: torch.Tensor, generator: torch.Generator | None = None
    ) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = hidden.float()
        if generator is not None and self.training:
            # A private training RNG avoids reseeding concurrent request-time sampling.
            return self._seeded_forward(self.complexity, hidden, generator), self._seeded_forward(
                self.sensitivity, hidden, generator
            )
        return self.complexity(hidden), self.sensitivity(hidden)

    @staticmethod
    def _seeded_forward(head: nn.Sequential, hidden: torch.Tensor, generator: torch.Generator):
        for layer in head:
            if isinstance(layer, nn.Dropout) and layer.training and layer.p:
                mask = (
                    torch.rand(hidden.shape, device=hidden.device, generator=generator) >= layer.p
                )
                hidden = hidden * mask / (1 - layer.p)
            else:
                hidden = layer(hidden)
        return hidden

    def isolated_copy(self, device: str | torch.device | None = None):
        result = copy.deepcopy(self)
        if device is not None:
            result.to(device)
        return result


@dataclass(frozen=True)
class HeadSnapshot:
    heads: DecisionHeads
    version: str
    complexity_temperature: float = 1.0
    sensitivity_temperature: float = 1.0
    global_round: int = 0


class DecisionHeadManager:
    """Atomic per-process pointer swap. Snapshots keep in-flight requests consistent.

    Each serving process owns one manager; cross-process activation must be coordinated
    by the deployment supervisor. Never mutate a published snapshot's parameters.
    """

    def __init__(self, initial: HeadSnapshot):
        self._lock = threading.RLock()
        self._active = self._prepare(initial)
        self._previous: HeadSnapshot | None = None

    @staticmethod
    def _prepare(snapshot: HeadSnapshot) -> HeadSnapshot:
        if not all(
            0 < t < float("inf")
            for t in [snapshot.complexity_temperature, snapshot.sensitivity_temperature]
        ):
            raise ValueError("Calibration temperatures must be finite and positive")
        heads = snapshot.heads.isolated_copy()
        if not all(torch.isfinite(p).all() for p in heads.parameters()):
            raise ValueError("Decision heads contain non-finite parameters")
        heads.float().eval().requires_grad_(False)
        return HeadSnapshot(
            heads,
            snapshot.version,
            snapshot.complexity_temperature,
            snapshot.sensitivity_temperature,
            snapshot.global_round,
        )

    def snapshot(self) -> HeadSnapshot:
        with self._lock:
            return self._active

    def activate(self, candidate: HeadSnapshot):
        prepared = self._prepare(candidate)
        with self._lock:
            expected = self._active.heads.state_dict()
            actual = prepared.heads.state_dict()
            if (
                prepared.heads.architecture_version != self._active.heads.architecture_version
                or set(actual) != set(expected)
                or any(actual[name].shape != expected[name].shape for name in actual)
            ):
                raise ValueError("Hot swap cannot change decision-head architecture or shape")
            prepared.heads.to(next(self._active.heads.parameters()).device)
            self._previous, self._active = self._active, prepared

    def rollback(self) -> bool:
        with self._lock:
            if self._previous is None:
                return False
            self._active, self._previous = self._previous, self._active
            return True


def predict(snapshot: HeadSnapshot, hidden: torch.Tensor):
    with torch.inference_mode():
        c, s = snapshot.heads(hidden)
        if not torch.isfinite(c).all() or not torch.isfinite(s).all():
            raise RuntimeError("Non-finite decision output; request cannot be routed")
        # A single small transfer of five probabilities; no hidden vectors leave the device.
        probabilities = (
            torch.cat(
                (
                    (c[0] / snapshot.complexity_temperature).softmax(-1),
                    (s[0] / snapshot.sensitivity_temperature).softmax(-1),
                )
            )
            .cpu()
            .tolist()
        )
    cp = dict(zip(Complexity, probabilities[:3], strict=True))
    sp = dict(zip(Sensitivity, probabilities[3:], strict=True))
    cl, sl = max(cp, key=cp.get), max(sp, key=sp.get)
    return (
        ComplexityPrediction(label=cl, probabilities=cp, confidence=cp[cl]),
        SensitivityPrediction(label=sl, probabilities=sp, confidence=sp[sl]),
    )
