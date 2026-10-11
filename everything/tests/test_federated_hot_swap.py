from concurrent.futures import ThreadPoolExecutor

from conftest import fixed_heads
from test_federated_checkpoint_validation import lifecycle

from edge_router.federation.checkpoint_manager import save_checkpoint
from edge_router.model.memory import parameter_hash
from edge_router.schemas import ChatRequest, Message


def test_hot_swap_and_rollback_preserve_backbone_and_shared_prefill(
    backend, service, extractor, examples, policy, tmp_path
):
    backbone_id, model_hash = id(backend.backbone), parameter_hash(backend.backbone)
    old_snapshot = backend.head_manager.snapshot()
    candidate = fixed_heads(backend, sensitivity="SENSITIVE", version="global-1", round=1)
    package = save_checkpoint(candidate, backend.identity, tmp_path / "global-1")
    manager = lifecycle(backend, extractor, examples, policy, tmp_path)
    assert manager.activate(package)
    assert backend.head_manager.snapshot().version == "global-1"
    assert old_snapshot.version == "tiny-bootstrap"
    backend.forward_trace.clear()
    before = backend.instrumentation.prefill_calls
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="Hello")], generation={"max_new_tokens": 3}
        )
    )
    assert result.decision.decision_model_version == "global-1"
    assert backend.instrumentation.prefill_calls - before == 1
    assert all(length == 1 for kind, length in backend.forward_trace if kind == "decode")
    assert id(backend.backbone) == backbone_id and parameter_hash(backend.backbone) == model_hash
    assert manager.rollback()
    assert backend.head_manager.snapshot().version == "tiny-bootstrap"


def test_inflight_request_consistent_across_swap(backend, service):
    original = backend.prefill

    def swapping_prefill(messages):
        context = original(messages)
        backend.head_manager.activate(fixed_heads(backend, complexity="HIGH", version="swapped"))
        return context

    backend.prefill = swapping_prefill
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="Hello")], generation={"max_new_tokens": 2}
        )
    )
    assert result.route == "LOCAL"
    assert result.decision.decision_model_version == "tiny-bootstrap"
    assert backend.head_manager.snapshot().version == "swapped"


def test_thread_safe_snapshot_swap(backend):
    def work(i):
        if i % 2:
            backend.head_manager.activate(fixed_heads(backend, version=f"v{i}"))
        snapshot = backend.head_manager.snapshot()
        return snapshot.version, sum(p.numel() for p in snapshot.heads.parameters())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(work, range(20)))
    assert len({count for version, count in results}) == 1


def test_durable_restore_and_replay_rejection(backend, extractor, examples, policy, tmp_path):
    package = save_checkpoint(
        fixed_heads(backend, sensitivity="SENSITIVE", version="global-1", round=1),
        backend.identity,
        tmp_path / "incoming-1",
    )
    manager = lifecycle(backend, extractor, examples, policy, tmp_path)
    assert manager.activate(package)
    # Reconstruct lifecycle and restore from the signed/validated immutable local stage.
    from edge_router.federation.feature_extractor import FrozenFeatureExtractor
    from edge_router.model.tiny_backend import TinyBackend

    restarted = TinyBackend()
    restored = lifecycle(restarted, FrozenFeatureExtractor(restarted), examples, policy, tmp_path)
    assert restored.restore_active()
    assert restarted.head_manager.snapshot().version == "global-1"
    assert restored.rollback()
    assert restarted.head_manager.snapshot().version == "tiny-bootstrap"
    assert not restored.activate(package)  # rollback must not reset the round high watermark
