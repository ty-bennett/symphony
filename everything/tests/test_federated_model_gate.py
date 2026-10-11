from conftest import fixed_heads

from edge_router.federation.model_gate import FederatedModelGate
from edge_router.federation.models import GateConfig


def test_bad_sensitivity_recall_rejected(backend, extractor, examples, policy):
    gate = FederatedModelGate(extractor, examples, policy, GateConfig())
    rejected = gate.evaluate(fixed_heads(backend, sensitivity="PUBLIC"))
    assert not rejected.accepted
    assert rejected.metrics["sensitivity"]["sensitive_false_negative_rate"] == 1
    assert gate.evaluate(fixed_heads(backend, sensitivity="SENSITIVE")).accepted


def test_missing_validation_classes_rejected(backend, extractor, examples, policy):
    gate = FederatedModelGate(
        extractor, [e for e in examples if e.sensitivity == "PUBLIC"], policy, GateConfig()
    )
    assert not gate.evaluate(backend.head_manager.snapshot()).accepted
