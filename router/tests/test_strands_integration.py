"""Opt-in, provisioned real Strands/Gemma 4 test; never part of offline CI."""

import os

import pytest

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings
from edge_router.schemas import GenerationOptions, Message


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_STRANDS_MODEL_TESTS") != "1",
    reason="Requires provisioned Gemma 4 + Strands weights",
)
def test_pretrained_strands_combined_prefill_and_cached_generation():
    settings = Settings.from_env()
    assert settings.model_backend == "gemma4_strands"
    backend = build_orchestrator(settings).backend
    before = backend.instrumentation.snapshot()
    context = backend.prefill([Message(role="user", content="Explain why the sky is blue.")])
    complexity, sensitivity = backend.classify(context, backend.head_manager.snapshot())
    assert len(complexity.probabilities) == 3 and len(sensitivity.probabilities) == 2
    # Exercise the cache independent of the predicted route. This is a local test,
    # not a runtime policy bypass, and transmits no request content anywhere.
    tokens = list(backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=3)))
    after = backend.instrumentation.snapshot()
    assert tokens
    assert after["prefill_calls"] - before["prefill_calls"] == 1
    assert after["prompt_tokens"] - before["prompt_tokens"] == context.prompt_length
    assert after["decoded_input_tokens"] - before["decoded_input_tokens"] == len(tokens) - 1
