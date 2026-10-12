import pytest
import torch

from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FeatureMode, FederationConfig
from edge_router.feedback.models import LabelSource
from edge_router.model.memory import parameter_hash


@pytest.mark.parametrize("mode", list(FeatureMode))
def test_training_changes_only_heads(backend, extractor, examples, mode):
    before, lm_before = parameter_hash(backend.backbone), parameter_hash(backend.lm_head)
    approved = backend.head_manager.snapshot()
    live_hash = parameter_hash(approved.heads)
    result = FederatedLocalTrainer(
        extractor, FederationConfig(feature_mode=mode, local_epochs=2)
    ).train(approved, examples)
    assert parameter_hash(result.candidate.heads) != live_hash
    assert parameter_hash(approved.heads) == live_hash
    assert parameter_hash(backend.backbone) == before
    assert parameter_hash(backend.lm_head) == lm_before
    assert all(not p.requires_grad and p.grad is None for p in backend.backbone.parameters())
    assert all(not p.requires_grad and p.grad is None for p in backend.lm_head.parameters())
    expected_prefills = len(examples) * (2 if mode == FeatureMode.ONLINE_FEATURE_EXTRACTION else 1)
    assert backend.instrumentation.prefill_calls == expected_prefills


def test_complexity_only_federation(backend, extractor, examples):
    approved = backend.head_manager.snapshot()
    result = FederatedLocalTrainer(extractor, FederationConfig(federate_sensitivity=False)).train(
        approved, examples
    )
    assert all(k.startswith("complexity.") for k in result.update.deltas)
    for key, value in approved.heads.sensitivity.state_dict().items():
        assert torch.equal(value, result.candidate.heads.sensitivity.state_dict()[key])


def test_untrusted_route_outcomes_do_not_train_sensitivity(backend, extractor, examples):
    for example in examples:
        example.label_source = LabelSource.ROUTE_OUTCOME
    result = FederatedLocalTrainer(extractor, FederationConfig()).train(
        backend.head_manager.snapshot(), examples
    )
    assert "sensitivity" not in result.update.head_example_counts
    assert all(k.startswith("complexity.") for k in result.update.deltas)


def test_update_clipping(backend, extractor, examples):
    result = FederatedLocalTrainer(extractor, FederationConfig(max_update_norm=0.00001)).train(
        backend.head_manager.snapshot(), examples
    )
    assert result.update.clipped
    assert 0 < result.update.update_norm <= 0.00001


def test_dp_is_never_silently_simulated(backend, extractor, examples):
    config = FederationConfig(privacy={"differential_privacy_enabled": True})
    with pytest.raises(NotImplementedError):
        FederatedLocalTrainer(extractor, config).train(backend.head_manager.snapshot(), examples)


def test_precomputed_features_require_no_new_prefill(backend, extractor, examples):
    features = [extractor.extract(example) for example in examples]
    before = backend.instrumentation.prefill_calls
    result = FederatedLocalTrainer(extractor, FederationConfig()).train(
        backend.head_manager.snapshot(), features=features
    )
    assert backend.instrumentation.prefill_calls == before
    assert result.update.num_examples == len(features)
    features[0].backbone_identity_hash = "wrong_revision"
    with pytest.raises(ValueError):
        FederatedLocalTrainer(extractor, FederationConfig()).train(
            backend.head_manager.snapshot(), features=features
        )


def test_inference_runs_during_isolated_local_training(backend, extractor, examples, service):
    from concurrent.futures import ThreadPoolExecutor

    from edge_router.schemas import ChatRequest, Message

    live = backend.head_manager.snapshot()
    config = FederationConfig(feature_mode=FeatureMode.ONLINE_FEATURE_EXTRACTION, local_epochs=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        training = pool.submit(FederatedLocalTrainer(extractor, config).train, live, examples)
        results = [
            service.run(
                ChatRequest(
                    messages=[Message(role="user", content="Hello")],
                    generation={"max_new_tokens": 2},
                )
            )
            for _ in range(3)
        ]
        assert training.result().update.num_examples == len(examples)
    assert all(result.status == "COMPLETED" for result in results)
    assert all(result.decision.decision_model_version == live.version for result in results)
    assert backend.head_manager.snapshot() is live
