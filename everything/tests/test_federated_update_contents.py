import json

import pytest
from safetensors.torch import load_file

from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FederationConfig
from edge_router.federation.update_builder import save_update, validate_update


def test_update_contains_only_head_deltas_and_aggregates(backend, extractor, examples, tmp_path):
    result = FederatedLocalTrainer(extractor, FederationConfig()).train(
        backend.head_manager.snapshot(), examples
    )
    save_update(result.update, tmp_path / "update")
    metadata = json.loads((tmp_path / "update" / "update.json").read_text())
    forbidden = {
        "messages",
        "prompts",
        "responses",
        "token_ids",
        "hidden_states",
        "embeddings",
        "features",
        "matches",
        "losses",
    }
    assert not forbidden.intersection(metadata)
    serialized = json.dumps(metadata)
    for example in examples:
        assert example.messages[0].content not in serialized
    tensors = load_file(str(tmp_path / "update" / "head_deltas.safetensors"))
    expected = backend.head_manager.snapshot().heads.state_dict()
    assert set(tensors) == set(expected)
    assert all(value.shape == expected[key].shape for key, value in tensors.items())
    with pytest.raises(AttributeError):
        result.update.prompts = ["forbidden"]
    result.update.aggregate_training_metrics["per_example_secret_encoded_as_number"] = 0.1
    with pytest.raises(ValueError):
        validate_update(result.update, backend.head_manager.snapshot(), backend.identity)
