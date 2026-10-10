"""Strands-compatible pointer readouts; no Strands serving framework dependency.

The published pointer formula is LayerNorm -> separate Q/K projections -> scaled
dot product (Apache-2.0 upstream). Two independent copies permit complexity-only
federation without changing sensitivity. Only these modules are trainable.
"""

import torch
from torch import nn

from edge_router.model.decision_heads import DecisionHeads


class StrandsPointerHead(nn.Module):
    def __init__(self, hidden_size: int, dim: int = 768, dropout: float = 0.05):
        super().__init__()
        self.norm = nn.LayerNorm(hidden_size)
        self.dropout = nn.Dropout(dropout)
        self.q = nn.Linear(hidden_size, dim)
        self.k = nn.Linear(hidden_size, dim)
        self.scale = dim**-0.5

    def forward(self, decide, options, generator: torch.Generator | None = None):
        def project(value, projection):
            value = self.norm(value.float())
            if generator is not None and self.training and self.dropout.p:
                mask = torch.rand(value.shape, device=value.device, generator=generator)
                value = value * (mask >= self.dropout.p) / (1 - self.dropout.p)
            else:
                value = self.dropout(value)
            return projection(value)

        query = project(decide, self.q).unsqueeze(-1)
        keys = project(options, self.k)
        return (keys @ query).squeeze(-1) * self.scale


class StrandsDecisionHeads(DecisionHeads):
    architecture_version = "strands-pointer-packed-7-v1"

    def __init__(self, hidden_size: int, width: int = 768, dropout: float = 0.05):
        nn.Module.__init__(self)
        self.hidden_size, self.width, self.dropout = hidden_size, width, dropout
        self.complexity = StrandsPointerHead(hidden_size, width, dropout)
        self.sensitivity = StrandsPointerHead(hidden_size, width, dropout)

    @property
    def feature_size(self) -> int:
        # [complexity answer, LOW, MEDIUM, HIGH, sensitivity answer, PUBLIC, SENSITIVE]
        return 7 * self.hidden_size

    def forward(self, hidden: torch.Tensor, generator: torch.Generator | None = None):
        if hidden.ndim != 2 or hidden.shape[-1] != self.feature_size:
            raise ValueError("Strands heads require seven selected final-layer vectors")
        vectors = hidden.float().reshape(hidden.shape[0], 7, self.hidden_size)
        return (
            self.complexity(vectors[:, 0], vectors[:, 1:4], generator),
            self.sensitivity(vectors[:, 4], vectors[:, 5:7], generator),
        )
