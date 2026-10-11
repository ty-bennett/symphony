import pytest
import torch

from edge_router.model.decision_heads import (
    DecisionHeadManager,
    DecisionHeads,
    HeadSnapshot,
    predict,
)
from edge_router.model.sampling import sample_token
from edge_router.schemas import GenerationOptions


def test_small_fp32_heads_probabilities():
    heads = DecisionHeads(32, width=16)
    manager = DecisionHeadManager(HeadSnapshot(heads, "test"))
    c, s = predict(manager.snapshot(), torch.randn(1, 32).half())
    assert sum(c.probabilities.values()) == pytest.approx(1)
    assert sum(s.probabilities.values()) == pytest.approx(1)
    assert c.confidence == max(c.probabilities.values())
    assert all(p.dtype == torch.float32 for p in manager.snapshot().heads.parameters())
    assert sum(p.numel() for p in heads.parameters()) < 2000


def test_sampling_greedy_and_nucleus():
    logits = torch.tensor([[1.0, 100.0, 3.0]])
    assert sample_token(logits, GenerationOptions()).item() == 1
    assert sample_token(logits, GenerationOptions(temperature=1, top_p=0.1)).item() == 1
    with pytest.raises(RuntimeError):
        sample_token(torch.tensor([[float("nan")]]), GenerationOptions())


def test_nonfinite_heads_rejected():
    heads = DecisionHeads(8)
    with torch.no_grad():
        next(heads.parameters()).fill_(float("nan"))
    with pytest.raises(ValueError):
        DecisionHeadManager(HeadSnapshot(heads, "bad"))
