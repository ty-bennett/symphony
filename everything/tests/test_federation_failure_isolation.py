from test_federated_checkpoint_validation import lifecycle

from edge_router.federation.adapter import NullFederationAdapter
from edge_router.federation.client import FederatedClient
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FederationConfig
from edge_router.schemas import ChatRequest, Message


def test_outage_does_not_stop_inference(backend, service, extractor, examples, policy, tmp_path):
    config = FederationConfig(enabled=True, secure_aggregation_required=False)
    checkpoints = lifecycle(backend, extractor, examples, policy, tmp_path)
    client = FederatedClient(
        NullFederationAdapter(), FederatedLocalTrainer(extractor, config), checkpoints, config
    )
    old = backend.head_manager.snapshot()
    assert not client.run_round(examples)
    assert client.status == "FEDERATION_ATTEMPT_FAILED"
    assert backend.head_manager.snapshot() is old
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="Hello")], generation={"max_new_tokens": 2}
        )
    )
    assert result.status == "COMPLETED"


def test_refuse_unsecured_federation(backend, extractor, examples, policy, tmp_path):
    config = FederationConfig(enabled=True, production=True, secure_aggregation_required=True)
    checkpoints = lifecycle(backend, extractor, examples, policy, tmp_path)
    client = FederatedClient(
        NullFederationAdapter(), FederatedLocalTrainer(extractor, config), checkpoints, config
    )
    assert not client.run_round(examples)
    assert client.status == "SECURE_AGGREGATION_UNAVAILABLE"
