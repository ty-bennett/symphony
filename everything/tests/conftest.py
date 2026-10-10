import pytest
import torch

from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.feedback.models import LocalExample
from edge_router.model.decision_heads import HeadSnapshot
from edge_router.model.tiny_backend import TinyBackend
from edge_router.routing.policy import PolicyConfig, RoutingPolicy
from edge_router.schemas import Message
from edge_router.service.orchestrator import Orchestrator


@pytest.fixture(autouse=True, scope="session")
def small_cpu_threads():
    # Tiny test tensors are much faster without a large BLAS thread pool.
    torch.set_num_threads(1)


@pytest.fixture
def backend():
    return TinyBackend()


@pytest.fixture
def policy():
    return RoutingPolicy(PolicyConfig.load("config/routing_policy.yaml", use_env=False))


@pytest.fixture
def service(backend, policy):
    return Orchestrator(backend, policy)


@pytest.fixture
def extractor(backend):
    return FrozenFeatureExtractor(backend)


@pytest.fixture
def examples():
    return [
        LocalExample(
            messages=[Message(role="user", content=f"Synthetic test {i}")],
            complexity="LOW",
            sensitivity="SENSITIVE" if i % 2 else "PUBLIC",
        )
        for i in range(8)
    ]


def fixed_heads(backend, complexity="LOW", sensitivity="PUBLIC", version="test-v1", round=0):
    heads = backend.head_manager.snapshot().heads.isolated_copy()
    with torch.no_grad():
        for parameter in heads.parameters():
            parameter.zero_()
        heads.complexity[-1].bias[{"LOW": 0, "MEDIUM": 1, "HIGH": 2}[complexity]] = 10
        heads.sensitivity[-1].bias[{"PUBLIC": 0, "SENSITIVE": 1}[sensitivity]] = 10
    return HeadSnapshot(heads, version, global_round=round)
