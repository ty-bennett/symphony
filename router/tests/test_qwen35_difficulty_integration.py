"""Full, locally provisioned export only. Never downloads weights for CI."""

import os
from pathlib import Path

import pytest

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings
from edge_router.model.memory import cache_bytes
from edge_router.schemas import GenerationOptions, Message


@pytest.mark.integration
def test_provisioned_qwen_awq_head_and_reusable_hybrid_cache():
    root = os.environ.get("QWEN_DIFFICULTY_QUANT_DIR")
    if not root:
        pytest.skip("Set QWEN_DIFFICULTY_QUANT_DIR to opt into the complete 2B checkpoint test")
    project = Path(__file__).resolve().parents[1]
    settings = Settings(
        model_id="Qwen/Qwen3.5-2B",
        model_revision="15852e8c16360a2fea060d615a32b45270f8a8fc",
        model_backend="qwen35_difficulty",
        device=os.environ.get("QWEN_DIFFICULTY_DEVICE", "cuda:0"),
        dtype="float16",
        quantization="modelopt_awq_int4",
        quantized_model_path=root,
        difficulty_checkpoint=str(project / "difficulty-head/out/heads/difficulty.pt"),
        difficulty_policy_config=str(project / "config/difficulty_policy.yaml"),
        max_context_tokens=256,
    )
    backend = build_orchestrator(settings).backend
    context = backend.prefill([Message(role="user", content="Reply with a short greeting.")])
    prediction = backend.classify(context, backend.head_manager.snapshot())
    assert prediction.layer == 10
    assert cache_bytes(context.past_key_values) > 0
    cache = context.past_key_values
    tokens = list(backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=3)))
    assert 1 <= len(tokens) <= 3
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.prompt_tokens == context.prompt_length
    assert backend.instrumentation.decoded_input_tokens == len(tokens) - 1
    assert context.past_key_values is cache
    assert backend.instrumentation.backbone_instances == 1
