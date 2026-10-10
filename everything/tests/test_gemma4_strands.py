"""Real tiny Gemma 4 + PEFT cache tests. No Hub downloads or enterprise data."""

import json
import os
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient
from peft import LoraConfig, get_peft_model
from safetensors.torch import save_file
from test_gemma_cache import tokenizer_fixture
from transformers import Gemma4Config, Gemma4ForCausalLM, Gemma4TextConfig

from edge_router.api.app import build_orchestrator, create_app
from edge_router.config import Settings
from edge_router.federation.checkpoint_manager import load_checkpoint, save_checkpoint
from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.model_gate import FederatedModelGate
from edge_router.federation.models import FederationConfig, GateConfig
from edge_router.model.decision_heads import DecisionHeads, HeadSnapshot
from edge_router.model.gemma4_strands_backend import (
    Gemma4StrandsBackend,
    file_hash,
    read_strands_artifacts,
)
from edge_router.model.memory import cache_bytes, parameter_hash
from edge_router.model.quantization.checkpoint import load_quantized_backend, save_quantized_backend
from edge_router.model.quantization.gemma4 import gemma4_linear_targets, quantize_backend
from edge_router.model.quantization.smoothquant import SmoothQuantInt8Linear
from edge_router.model.strands_heads import StrandsDecisionHeads, StrandsPointerHead
from edge_router.model.strands_prompt import StrandsPromptConfig, encode_strands_prompt
from edge_router.routing.policy import RoutingPolicy
from edge_router.schemas import (
    ChatRequest,
    ComplexityPrediction,
    GenerationOptions,
    Message,
    SensitivityPrediction,
)
from edge_router.service.orchestrator import Orchestrator

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


@pytest.fixture
def strands_backend():
    with torch.random.fork_rng():
        torch.manual_seed(42)
        config = Gemma4TextConfig(
            vocab_size=10,
            vocab_size_per_layer_input=10,
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=4,
            num_attention_heads=2,
            num_key_value_heads=1,
            head_dim=16,
            max_position_embeddings=2048,
            sliding_window=8,
            hidden_size_per_layer_input=4,
            num_kv_shared_layers=2,
            layer_types=["sliding_attention", "full_attention"] * 2,
            final_logit_softcapping=4.0,
        )
        config._attn_implementation = "eager"
        model = Gemma4ForCausalLM(config)
        model.model = get_peft_model(
            model.model,
            LoraConfig(
                r=2,
                lora_alpha=4,
                target_modules=TARGETS,
                task_type="FEATURE_EXTRACTION",
            ),
        )
        # Nonzero adapter effects: test reuse with genuinely adapted K/V, not zero LoRA.
        for name, value in model.model.named_parameters():
            if "lora_B" in name:
                torch.nn.init.normal_(value, std=0.01)
        heads = StrandsDecisionHeads(32, width=8, dropout=0.05)
    tokenizer = tokenizer_fixture()
    tokenizer.bos_token = "[UNK]"  # Tiny fixture's token 2 represents Gemma's BOS.
    tokenizer.chat_template = "{{ bos_token }}" + tokenizer.chat_template
    result = Gemma4StrandsBackend(
        model,
        tokenizer,
        Settings(model_id="random-gemma4", max_context_tokens=2048),
        heads,
        StrandsPromptConfig.load("config/strands_requirements.yaml"),
        "synthetic-strands-v1",
        temperature=0.77,
        adapter_hash="synthetic-adapter-hash",
    )
    result.eos_ids.clear()
    return result


def local_service(backend, policy):
    # Uniform pretrained-head stand-in plus a LOCAL uncertainty fallback (never FRONTIER).
    heads = backend.head_manager.snapshot().heads.isolated_copy()
    with torch.no_grad():
        for p in heads.parameters():
            p.zero_()
    backend.head_manager.activate(HeadSnapshot(heads, "local-test"))
    return Orchestrator(
        backend, RoutingPolicy(policy.config.model_copy(update={"uncertain_route": "LOCAL"}))
    )


def test_one_prefill_and_one_token_decode_inputs(strands_backend, policy):
    backend = strands_backend
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda module, args, kwargs: trace.append(kwargs["input_ids"].clone()), with_kwargs=True
    )
    result = local_service(backend, policy).run(
        ChatRequest(
            messages=[Message(role="user", content="hello world")],
            generation={"max_new_tokens": 5},
        )
    )
    hook.remove()
    assert result.route == "LOCAL"
    assert [x.shape[1] for x in trace] == [result.decision.prompt_tokens, 1, 1, 1, 1]
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.prompt_tokens == trace[0].numel()
    assert backend.instrumentation.decoded_input_tokens == 4
    assert backend.instrumentation.backbone_instances == 1
    assert backend.backbone is backend.model.model
    assert backend.lm_head is backend.model.lm_head
    assert all(not p.requires_grad for p in backend.model.parameters())


def test_selected_readouts_and_generation_share_one_final_layer_output(strands_backend):
    backend = strands_backend
    captured = []
    hook = backend.backbone.register_forward_hook(
        lambda module, args, output: captured.append(output.last_hidden_state)
    )
    context = backend.prefill([Message(role="user", content="hello world")])
    hook.remove()
    assert len(captured) == 1
    states = captured[0]
    positions = context.metadata["decision_positions"]
    torch.testing.assert_close(
        context.metadata["decision_features"], states[:, positions, :].reshape(1, -1)
    )
    torch.testing.assert_close(context.final_hidden_state, states[:, -1, :])
    assert context.metadata["decision_features"].untyped_storage().nbytes() == 7 * 32 * 4
    c, s = backend.classify(context, backend.head_manager.snapshot())
    assert set(c.probabilities) == {"LOW", "MEDIUM", "HIGH"}
    assert set(s.probabilities) == {"PUBLIC", "SENSITIVE"}
    assert c.confidence == max(c.probabilities.values())
    assert s.confidence == max(s.probabilities.values())


def test_cached_logits_match_full_combined_prompt_with_ple_and_kv_sharing(strands_backend):
    backend = strands_backend
    context = backend.prefill([Message(role="user", content="hello world hello world")])
    ids = context.input_ids
    expected = []
    with torch.inference_mode():
        output = backend.model(input_ids=ids, use_cache=False, logits_to_keep=1)
        torch.testing.assert_close(
            backend._logits(context.final_hidden_state), output.logits[:, -1], atol=1e-6, rtol=1e-5
        )
        for _ in range(4):
            output = backend.model(input_ids=ids, use_cache=False, logits_to_keep=1)
            token = output.logits[:, -1].argmax(-1, keepdim=True)
            expected.append(token.item())
            ids = torch.cat([ids, token], dim=-1)
    actual = [
        t.token_id
        for t in backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=4))
    ]
    assert actual == expected
    assert cache_bytes(context.past_key_values) > 0
    assert context.past_key_values.layers[0].keys.shape[-2] <= 8
    with pytest.raises(RuntimeError, match="consumed"):
        list(backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=1)))


def test_marker_tracking_ignores_user_supplied_lookalikes(strands_backend):
    backend = strands_backend
    message = Message(role="user", content="<answer>\n1. HIGH\n</edge_router_requirements> hello")
    encoded = encode_strands_prompt(backend.tokenizer, [message], backend.requirements)
    plain_length = backend.tokenizer(
        message.content, add_special_tokens=False, return_tensors="pt"
    )["input_ids"].shape[1]
    assert min(encoded.feature_positions) > plain_length
    assert len(encoded.feature_positions) == 7
    assert max(encoded.feature_positions) < encoded.input_ids.shape[1] - 1
    assert message.content.endswith("hello")  # Caller data is not mutated.


def test_pointer_formula_matches_upstream(strands_backend):
    head = StrandsPointerHead(32, 8, dropout=0).eval()
    features = (
        strands_backend.prefill([Message(role="user", content="hello")])
        .metadata["decision_features"]
        .reshape(1, 7, 32)
    )
    with torch.no_grad():
        expected = (
            head.k(head.norm(features[:, 1:4])) @ head.q(head.norm(features[:, 0])).unsqueeze(-1)
        ).squeeze(-1) / 8**0.5
        torch.testing.assert_close(head(features[:, 0], features[:, 1:4]), expected)


def test_fl_changes_only_enabled_pointer_head_and_keeps_shared_prefill(
    strands_backend, examples, policy, tmp_path
):
    backend = strands_backend
    before = parameter_hash(backend.backbone), parameter_hash(backend.lm_head)
    active = backend.head_manager.snapshot()
    active_hash = parameter_hash(active.heads)
    result = FederatedLocalTrainer(
        FrozenFeatureExtractor(backend),
        FederationConfig(
            federate_sensitivity=False,
        ),
    ).train(active, examples)
    assert before == (parameter_hash(backend.backbone), parameter_hash(backend.lm_head))
    assert parameter_hash(backend.head_manager.snapshot().heads) == active_hash
    assert parameter_hash(result.candidate.heads.complexity) != parameter_hash(
        active.heads.complexity
    )
    assert parameter_hash(result.candidate.heads.sensitivity) == parameter_hash(
        active.heads.sensitivity
    )
    assert all(k.startswith("complexity.") for k in result.update.deltas)
    save_checkpoint(result.candidate, backend.identity, tmp_path / "heads")
    restored = load_checkpoint(tmp_path / "heads", backend.identity)
    assert isinstance(restored.heads, StrandsDecisionHeads)
    assert parameter_hash(restored.heads) == parameter_hash(result.candidate.heads)
    original_backbone = backend.backbone
    backend.head_manager.activate(restored)
    assert backend.backbone is original_backbone
    before_calls = backend.instrumentation.prefill_calls
    local_service(backend, policy).run(
        ChatRequest(
            messages=[Message(role="user", content="hello")],
            generation={"max_new_tokens": 2},
        )
    )
    assert backend.instrumentation.prefill_calls - before_calls == 1
    assert backend.head_manager.rollback()


@pytest.mark.parametrize(
    "field,value", [("backbone_adapter_hash", "other"), ("decision_prompt_hash", "changed")]
)
def test_pointer_checkpoint_rejects_adapter_or_rubric_changes(
    strands_backend, tmp_path, field, value
):
    backend = strands_backend
    save_checkpoint(backend.head_manager.snapshot(), backend.identity, tmp_path / "heads")
    with pytest.raises(ValueError, match="adapter or decision rubric"):
        load_checkpoint(tmp_path / "heads", {**backend.identity, field: value})
    assert backend.head_manager.snapshot().version == "synthetic-strands-v1"


def test_incompatible_mlp_cannot_replace_pointer_head(strands_backend):
    with pytest.raises(ValueError, match="architecture"):
        strands_backend.head_manager.activate(HeadSnapshot(DecisionHeads(32, width=8), "wrong"))


def test_candidate_validation_supports_pointer_features(strands_backend, examples, policy):
    gate = FederatedModelGate(
        FrozenFeatureExtractor(strands_backend),
        examples,
        policy,
        GateConfig(
            minimum_complexity_accuracy=0,
            minimum_sensitivity_recall=0,
            maximum_sensitive_false_negative_rate=1,
        ),
    )
    # Permissive thresholds test the architecture plumbing, not production quality.
    result = gate.evaluate(strands_backend.head_manager.snapshot())
    assert result.accepted and result.metrics["routing"]["deterministic_invariant_violations"] == 0
    assert (
        gate.evaluate(HeadSnapshot(DecisionHeads(32), "incompatible")).reason
        == "INCOMPATIBLE_HEAD_ARCHITECTURE"
    )


@pytest.mark.parametrize(
    "content,classification,expected",
    [
        ("hello", "UNKNOWN", "FRONTIER"),
        ("password=synthetic_example_secret", "UNKNOWN", "PRIVATE_AWS"),
        ("hello", "SENSITIVE", "PRIVATE_AWS"),
    ],
)
def test_policy_remains_authoritative_and_handoff_never_generates(
    strands_backend, policy, monkeypatch, content, classification, expected, caplog
):
    backend = strands_backend
    classify = backend.classify

    def confidently_public(context, snapshot):
        classify(context, snapshot)  # Still execute the real pretrained-shaped readout.
        return (
            ComplexityPrediction(
                label="HIGH", probabilities={"LOW": 0, "MEDIUM": 0, "HIGH": 1}, confidence=1
            ),
            SensitivityPrediction(
                label="PUBLIC", probabilities={"PUBLIC": 1, "SENSITIVE": 0}, confidence=1
            ),
        )

    monkeypatch.setattr(backend, "classify", confidently_public)

    def forbidden_lm_projection(module, args):
        pytest.fail("Handoff must never invoke the LM head")

    hook = backend.lm_head.register_forward_pre_hook(forbidden_lm_projection)
    result = Orchestrator(backend, policy).run(
        ChatRequest(
            messages=[Message(role="user", content=content)],
            security_context={"classification": classification},
        )
    )
    hook.remove()
    assert result.route == expected and result.status == "HANDOFF_REQUIRED"
    assert backend.instrumentation.prefill_calls == 1 and backend.instrumentation.decode_calls == 0
    assert content not in caplog.text
    assert "synthetic_example_secret" not in caplog.text


def test_api_uses_combined_prompt_decision_without_decode(strands_backend, policy):
    app = create_app(Orchestrator(strands_backend, policy))
    with TestClient(app) as client:
        info = client.get("/v1/model/info").json()
        response = client.post(
            "/v1/decision", json={"messages": [{"role": "user", "content": "hello"}]}
        )
    assert info["backend"] == "gemma4_strands"
    assert response.status_code == 200
    assert "decision_features" not in response.text
    assert strands_backend.instrumentation.prefill_calls == 1
    assert strands_backend.instrumentation.decode_calls == 0


def test_context_rejection_never_runs_transformer(strands_backend):
    strands_backend.settings.max_context_tokens = 2
    with pytest.raises(ValueError, match="Combined prompt"):
        strands_backend.prefill([Message(role="user", content="hello")])
    assert strands_backend.instrumentation.prefill_calls == 0
    with pytest.raises(ValueError, match="end in"):
        strands_backend.prefill([Message(role="assistant", content="hello")])


@pytest.fixture
def provisioned_strands(strands_backend, tmp_path):
    backend = strands_backend
    base_path, package_path = tmp_path / "base", tmp_path / "strands"
    base_path.mkdir()
    package_path.mkdir()
    base = backend.backbone.get_base_model()
    # Export original base weights without adapters, using the real multimodal naming.
    state = {}
    for name, value in base.state_dict().items():
        if "lora_" not in name:
            state["model.language_model." + name.replace(".base_layer", "")] = (
                value.detach().clone()
            )
    state["model.vision_tower.synthetic_unused"] = torch.zeros(1)
    Gemma4Config(text_config=backend.model.config.to_dict()).to_json_file(base_path / "config.json")
    save_file(state, str(base_path / "model.safetensors"), metadata={"format": "pt"})
    backend.tokenizer.save_pretrained(base_path)
    backend.backbone.save_pretrained(package_path / "lora")
    save_file(
        {
            k: v.clone()
            for k, v in backend.head_manager.snapshot().heads.complexity.state_dict().items()
        },
        str(package_path / "head.safetensors"),
    )
    config = {
        "base_model": str(base_path),
        "head_type": "pointer",
        "use_lora": True,
        "pointer_dim": 8,
        "head_dropout": 0.05,
        "temperature": 0.77,
        "lora_r": 2,
        "lora_alpha": 4,
        "lora_targets": TARGETS,
    }
    (package_path / "strands_decider_config.json").write_text(json.dumps(config))
    provenance = {
        "base_model": str(base_path),
        "base_model_revision": "a" * 40,
        "adapter_sha256": file_hash(package_path / "lora/adapter_model.safetensors"),
        "head_sha256": file_hash(package_path / "head.safetensors"),
    }
    (package_path / "provenance.json").write_text(json.dumps(provenance))
    return Settings(
        model_id=str(base_path),
        model_backend="gemma4_strands",
        strands_checkpoint=str(package_path),
        local_files_only=True,
        max_context_tokens=2048,
    )


@pytest.mark.parametrize("dtype", ["float32", "float16"])
def test_actual_text_only_loader_and_peft_restore(provisioned_strands, strands_backend, dtype):
    settings = provisioned_strands.model_copy(update={"dtype": dtype})
    loaded = build_orchestrator(settings).backend
    assert loaded.identity["base_model_revision"] == "a" * 40
    assert not hasattr(loaded.model, "vision_tower")
    assert loaded.backbone is loaded.model.model
    assert all(not p.requires_grad for p in loaded.model.parameters())
    assert all(p.dtype == getattr(torch, dtype) for p in loaded.model.parameters())
    assert all(p.dtype == torch.float32 for p in loaded.head_manager.snapshot().heads.parameters())
    for key, value in strands_backend.model.state_dict().items():
        torch.testing.assert_close(loaded.model.state_dict()[key], value.to(getattr(torch, dtype)))
    context = loaded.prefill([Message(role="user", content="hello")])
    assert context.past_key_values is not None
    assert loaded.classify(context, loaded.head_manager.snapshot())[0].label in {
        "LOW",
        "MEDIUM",
        "HIGH",
    }


def test_fp16_cache_and_fp32_readouts_preserve_single_prefill(provisioned_strands):
    settings = provisioned_strands.model_copy(update={"dtype": "float16"})
    backend = build_orchestrator(settings).backend
    backend.eos_ids.clear()
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda module, args, kwargs: trace.append(kwargs["input_ids"].shape[1]), with_kwargs=True
    )
    try:
        context = backend.prefill([Message(role="user", content="hello world")])
        assert context.final_hidden_state.dtype == torch.float16
        assert context.metadata["decision_features"].dtype == torch.float16
        cache_tensors = [
            value
            for layer in context.past_key_values.layers
            for value in (layer.keys, layer.values)
            if value is not None
        ]
        assert cache_tensors
        assert all(value.dtype == torch.float16 for value in cache_tensors)
        with torch.inference_mode():
            logits = backend.head_manager.snapshot().heads(context.metadata["decision_features"])
        assert all(value.dtype == torch.float32 and torch.isfinite(value).all() for value in logits)
        complexity, sensitivity = backend.classify(context, backend.head_manager.snapshot())
        for prediction in (complexity, sensitivity):
            assert sum(prediction.probabilities.values()) == pytest.approx(1.0)
        tokens = list(backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=4)))
    finally:
        hook.remove()
    assert len(tokens) == 4
    assert trace == [context.prompt_length, 1, 1, 1]
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.prompt_tokens == context.prompt_length
    assert backend.instrumentation.decoded_input_tokens == 3


@pytest.fixture
def smoothquant_backend(provisioned_strands, tmp_path):
    settings = provisioned_strands.model_copy(update={"dtype": "float16"})
    backend = build_orchestrator(settings).backend
    before_heads = parameter_hash(backend.head_manager.snapshot().heads)
    before_lm = parameter_hash(backend.lm_head)
    recipe = quantize_backend(backend, [[Message(role="user", content="hello world")]])
    assert parameter_hash(backend.head_manager.snapshot().heads) == before_heads
    assert parameter_hash(backend.lm_head) == before_lm
    destination = save_quantized_backend(backend, recipe, tmp_path / "int8")
    loaded = build_orchestrator(
        settings.model_copy(
            update={
                "quantization": "smoothquant_int8",
                "quantized_model_path": str(destination),
            }
        )
    ).backend
    loaded.eos_ids.clear()
    return loaded


def test_smoothquant_reloads_actual_int8_without_loading_base(smoothquant_backend, monkeypatch):
    backend = smoothquant_backend
    assert all(
        isinstance(m, SmoothQuantInt8Linear) for m in gemma4_linear_targets(backend.model).values()
    )
    assert all(
        m.weight_int8.dtype == torch.int8 for m in gemma4_linear_targets(backend.model).values()
    )
    assert not hasattr(backend.backbone, "peft_config")
    assert all(not p.requires_grad for p in backend.model.parameters())
    assert backend.model.model.embed_tokens.weight is backend.model.lm_head.weight
    assert backend.model_info()["quantization"] == "smoothquant_int8"
    assert backend.model_info()["adapter_merged"] is True

    def forbidden(*args, **kwargs):
        raise AssertionError("INT8 serving must not load original model or Strands weights")

    monkeypatch.setattr(Gemma4ForCausalLM, "from_pretrained", forbidden)
    import edge_router.model.gemma4_strands_backend as adapter

    monkeypatch.setattr(adapter, "read_strands_artifacts", forbidden)
    reloaded = build_orchestrator(backend.settings).backend
    assert parameter_hash(reloaded.model) == parameter_hash(backend.model)


def test_smoothquant_shared_prefill_after_hot_swap(smoothquant_backend, policy):
    backend = smoothquant_backend
    model_before = parameter_hash(backend.model)
    service = local_service(backend, policy)  # Atomic decision-head swap only.
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda m, args, kwargs: trace.append(kwargs["input_ids"].shape[1]), with_kwargs=True
    )
    try:
        result = service.run(
            ChatRequest(
                messages=[Message(role="user", content="hello world")],
                generation={"max_new_tokens": 4},
            )
        )
    finally:
        hook.remove()
    assert result.route == "LOCAL"
    assert trace == [result.decision.prompt_tokens, 1, 1, 1]
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.backbone_instances == 1
    assert parameter_hash(backend.model) == model_before


def test_smoothquant_fl_freezes_integer_weights_and_binds_updates(smoothquant_backend, examples):
    from edge_router.federation.update_builder import validate_update

    backend = smoothquant_backend
    model_before = parameter_hash(backend.model)
    active = backend.head_manager.snapshot()
    heads_before = parameter_hash(active.heads)
    result = FederatedLocalTrainer(
        FrozenFeatureExtractor(backend), FederationConfig(federate_sensitivity=False)
    ).train(active, examples)
    assert parameter_hash(backend.model) == model_before
    assert parameter_hash(backend.head_manager.snapshot().heads) == heads_before
    assert parameter_hash(result.candidate.heads.complexity) != parameter_hash(
        active.heads.complexity
    )
    assert (
        result.update.backbone_quantization_hash == backend.identity["backbone_quantization_hash"]
    )
    with pytest.raises(ValueError, match="quantized-backbone identity"):
        validate_update(
            result.update,
            active,
            {**backend.identity, "backbone_quantization_hash": "different-artifact"},
        )


def test_smoothquant_cached_logits_match_full_prefix(smoothquant_backend):
    backend = smoothquant_backend
    context = backend.prefill([Message(role="user", content="hello world")])
    with torch.inference_mode():
        reference = backend.model(input_ids=context.input_ids, use_cache=False, logits_to_keep=1)
        torch.testing.assert_close(
            backend._logits(context.final_hidden_state),
            reference.logits[:, -1],
            atol=1e-3,
            rtol=1e-3,
        )
        token = reference.logits[:, -1].argmax(-1, keepdim=True)
        cached = backend._forward(
            token,
            torch.cat([context.attention_mask, torch.ones_like(token)], dim=1),
            context.past_key_values,
            context.prompt_length,
        )
        full = backend.model(
            input_ids=torch.cat([context.input_ids, token], dim=1),
            use_cache=False,
            logits_to_keep=1,
        )
        torch.testing.assert_close(
            backend._logits(cached.last_hidden_state[:, -1]),
            full.logits[:, -1],
            atol=5e-3,
            rtol=5e-3,
        )
    assert all(
        v.dtype == torch.float16
        for layer in context.past_key_values.layers
        for v in (layer.keys, layer.values)
        if v is not None
    )


def test_smoothquant_keeps_security_overrides(smoothquant_backend, policy, caplog):
    backend = smoothquant_backend
    # Deliberately wrong learned output; hard security must still win.
    backend.classify = lambda *args: (
        ComplexityPrediction(
            label="HIGH",
            probabilities={"LOW": 0.001, "MEDIUM": 0.001, "HIGH": 0.998},
            confidence=0.998,
        ),
        SensitivityPrediction(
            label="PUBLIC", probabilities={"PUBLIC": 0.999, "SENSITIVE": 0.001}, confidence=0.999
        ),
    )
    service = Orchestrator(backend, policy)
    for message, security in [
        ("password=SYNTHETIC_EXAMPLE_ONLY", {}),
        ("hello world", {"classification": "SENSITIVE"}),
    ]:
        result = service.run(
            ChatRequest(messages=[Message(role="user", content=message)], security_context=security)
        )
        assert result.route == "PRIVATE_AWS"
    assert backend.instrumentation.decode_calls == 0
    assert "SYNTHETIC_EXAMPLE_ONLY" not in caplog.text


def test_smoothquant_artifact_corruption_and_identity_rejected(smoothquant_backend, tmp_path):
    backend = smoothquant_backend
    with pytest.raises(ValueError, match="Incompatible SmoothQuant"):
        load_quantized_backend(backend.settings.model_copy(update={"model_revision": "b" * 40}))
    save_checkpoint(backend.head_manager.snapshot(), backend.identity, tmp_path / "heads-int8")
    with pytest.raises(ValueError, match="quantized backbone"):
        load_checkpoint(
            tmp_path / "heads-int8", {**backend.identity, "backbone_quantization_hash": None}
        )
    artifact = Path(backend.settings.quantized_model_path) / "model.safetensors"
    artifact.write_bytes(b"SYNTHETIC_CORRUPTION")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_quantized_backend(backend.settings)


def test_smoothquant_requires_artifact_and_explicit_calibration(strands_backend):
    with pytest.raises(ValueError, match="QUANTIZED_MODEL_PATH"):
        load_quantized_backend(Settings(quantization="smoothquant_int8"))
    with pytest.raises(ValueError, match="requires examples"):
        quantize_backend(strands_backend, [])


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_SMOOTHQUANT_CUDA_TESTS") != "1", reason="Explicit CUDA/Jetson opt-in required"
)
def test_smoothquant_cuda_cache_reuse(smoothquant_backend, policy):
    assert torch.cuda.is_available(), "CUDA was requested but is unavailable"
    backend = load_quantized_backend(
        smoothquant_backend.settings.model_copy(update={"device": "cuda"})
    )
    backend.eos_ids.clear()
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda m, args, kwargs: trace.append(kwargs["input_ids"].shape[1]), with_kwargs=True
    )
    try:
        result = local_service(backend, policy).run(
            ChatRequest(
                messages=[Message(role="user", content="hello world")],
                generation={"max_new_tokens": 4},
            )
        )
    finally:
        hook.remove()
    torch.cuda.synchronize()
    assert result.route == "LOCAL"
    assert trace == [result.decision.prompt_tokens, 1, 1, 1]
    assert backend.instrumentation.prefill_calls == 1


@pytest.mark.parametrize("corruption", ["hash", "revision", "backbone", "head_type"])
def test_checkpoint_validation_before_model_loading(provisioned_strands, corruption):
    settings = provisioned_strands
    directory = Path(settings.strands_checkpoint)
    if corruption == "hash":
        (directory / "head.safetensors").write_bytes(b"invalid-artifact")
    elif corruption == "revision":
        settings = settings.model_copy(update={"model_revision": "b" * 40})
    elif corruption == "backbone":
        settings = settings.model_copy(update={"model_id": "incompatible"})
    else:
        path = directory / "strands_decider_config.json"
        value = json.loads(path.read_text())
        value["head_type"] = "slot"
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_strands_artifacts(settings)
