"""Real tiny Qwen hybrid caches, trained artifact loading, and regression policy."""

import hashlib
import json
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient
from test_gemma_cache import tokenizer_fixture
from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig

from edge_router.api.app import create_app
from edge_router.config import Settings
from edge_router.model.decision_heads import HeadSnapshot
from edge_router.model.difficulty_head import DifficultyHead, load_difficulty_head
from edge_router.model.memory import parameter_hash
from edge_router.model.qwen35_difficulty_backend import Qwen35DifficultyBackend
from edge_router.routing.difficulty_policy import DifficultyPolicyConfig, DifficultyRoutingPolicy
from edge_router.schemas import (
    ChatRequest,
    DifficultyPrediction,
    GenerationOptions,
    Message,
    SecurityContext,
)
from edge_router.service.orchestrator import Orchestrator
from edge_router.testing.harness import EvaluationCase, ModelTestHarness, summarize


def small_config(hidden=16):
    return Qwen3_5TextConfig(
        vocab_size=10,
        hidden_size=hidden,
        intermediate_size=hidden * 2,
        num_hidden_layers=4,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=hidden // 2,
        linear_key_head_dim=hidden // 2,
        linear_value_head_dim=hidden // 2,
        linear_num_key_heads=2,
        linear_num_value_heads=2,
        max_position_embeddings=128,
        eos_token_id=1,
        pad_token_id=0,
        partial_rotary_factor=1.0,
        rope_parameters={
            "rope_type": "default",
            "rope_theta": 10000,
            "partial_rotary_factor": 1.0,
            "mrope_section": [1, 1, 2],
        },
    )


@pytest.fixture
def qwen():
    tokenizer = tokenizer_fixture()
    settings = Settings(
        model_backend="qwen35_difficulty",
        model_id="synthetic-qwen",
        model_revision="test",
        max_context_tokens=128,
    )
    identity = {
        "base_repo": settings.model_id,
        "base_revision": settings.model_revision,
        "chat_template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
        "manifest_sha256": "synthetic-manifest",
        "device_max_prompt_tokens": 128,
        "L_d": 1,
    }
    with torch.random.fork_rng():
        torch.manual_seed(23)
        model = Qwen3_5ForCausalLM(small_config())
        head = DifficultyHead(16, 1, identity)
    with torch.no_grad():
        for parameter in head.parameters():
            parameter.zero_()
        head.network[-1].bias.fill_(-1)
    backend = Qwen35DifficultyBackend(model, tokenizer, settings, HeadSnapshot(head, "test-v1"))
    backend.eos_ids.clear()
    return backend


def service(qwen):
    return Orchestrator(qwen, DifficultyRoutingPolicy(DifficultyPolicyConfig()))


def test_each_layer_prefills_prompt_once_and_decodes_only_new_tokens(qwen):
    traces, handles = [[] for _ in qwen.backbone.layers], []
    for index, layer in enumerate(qwen.backbone.layers):
        handles.append(
            layer.register_forward_pre_hook(
                lambda module, args, kwargs, i=index: traces[i].append(
                    args[0].shape[1] if args else kwargs["hidden_states"].shape[1]
                ),
                with_kwargs=True,
            )
        )
    result = service(qwen).run(
        ChatRequest(
            messages=[Message(role="user", content="hello world")],
            generation=GenerationOptions(max_new_tokens=4),
        )
    )
    for handle in handles:
        handle.remove()
    assert result.route == "LOCAL"
    assert result.decision.difficulty.score == -1
    assert result.decision.complexity is None and result.decision.sensitivity is None
    assert all(trace == [result.decision.prompt_tokens, 1, 1, 1] for trace in traces)
    assert qwen.instrumentation.prefill_calls == 1
    assert qwen.instrumentation.prompt_tokens == result.decision.prompt_tokens
    assert qwen.instrumentation.decoded_input_tokens == 3


def test_cache_decode_matches_full_recomputation_and_captures_residual(qwen):
    captured = []
    hook = qwen.backbone.layers[1].register_forward_hook(
        lambda module, args, out: captured.append(out[:, -1, :].detach().clone())
    )
    context = qwen.prefill([Message(role="user", content="hello world")])
    hook.remove()
    assert torch.equal(context.decision_hidden_state, captured[0])
    assert not torch.equal(context.decision_hidden_state, context.final_hidden_state)
    assert context.past_key_values.get_seq_length() == context.prompt_length
    assert any(
        getattr(layer, "recurrent_states", None) is not None
        for layer in context.past_key_values.layers
    )
    original_cache = context.past_key_values
    actual = [
        token.token_id
        for token in qwen.decode_from_prefill(context, GenerationOptions(max_new_tokens=4))
    ]
    assert context.past_key_values is original_cache
    ids, expected = context.input_ids, []
    with torch.inference_mode():
        for _ in range(4):
            token = (
                qwen.model(input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False)
                .logits[:, -1, :]
                .argmax(-1, keepdim=True)
            )
            expected.append(token.item())
            ids = torch.cat((ids, token), -1)
    assert actual == expected
    with pytest.raises(RuntimeError, match="consumed"):
        list(qwen.decode_from_prefill(context, GenerationOptions(max_new_tokens=1)))


def test_thinking_disabled_no_classifier_prompt_and_no_all_layer_states(qwen, monkeypatch):
    original = qwen.tokenizer.apply_chat_template

    def check(messages, **kwargs):
        assert messages == [{"role": "user", "content": "hello world"}]
        assert kwargs["enable_thinking"] is False and kwargs["add_generation_prompt"] is True
        return original(messages, **kwargs)

    monkeypatch.setattr(qwen.tokenizer, "apply_chat_template", check)
    trace = []
    hook = qwen.backbone.register_forward_pre_hook(
        lambda module, args, kwargs: trace.append(kwargs["output_hidden_states"]), with_kwargs=True
    )
    service(qwen).decide(ChatRequest(messages=[Message(role="user", content="hello world")]))
    hook.remove()
    assert trace == [False]


def test_aws_and_decision_only_never_call_lm_head(qwen, monkeypatch):
    monkeypatch.setattr(qwen.lm_head, "forward", lambda *args: pytest.fail("LM head must not run"))
    router = service(qwen)
    request = ChatRequest(messages=[Message(role="user", content="hello")])
    assert router.decide(request).route == "LOCAL"
    head = qwen.head_manager.snapshot().heads.isolated_copy()
    with torch.no_grad():
        head.network[-1].bias.fill_(2)
    qwen.head_manager.activate(HeadSnapshot(head, "test-v2"))
    result = router.run(request)
    assert result.route == "PRIVATE_AWS" and result.status == "HANDOFF_REQUIRED"
    assert result.generated_tokens == 0 and result.text is None
    assert qwen.instrumentation.decode_calls == 0


@pytest.mark.parametrize("classification", ["PUBLIC", "SENSITIVE", "UNKNOWN"])
@pytest.mark.parametrize("flags", [[], ["private_key"]])
@pytest.mark.parametrize("score", [-3, 0, 0.01, 20])
def test_regression_policy_never_has_a_separate_frontier_destination(classification, flags, score):
    policy = DifficultyRoutingPolicy(DifficultyPolicyConfig())
    result = policy.decide(
        DifficultyPrediction(score=score, layer=10),
        flags,
        SecurityContext(classification=classification),
    )
    assert result.route == ("LOCAL" if score <= 0 else "PRIVATE_AWS")
    assert result.route != "FRONTIER"
    assert result.resolved_sensitivity == (
        "SENSITIVE" if flags or classification != "PUBLIC" else "PUBLIC"
    )


def test_policy_cannot_be_reconfigured_to_frontier():
    with pytest.raises(ValueError):
        DifficultyRoutingPolicy(DifficultyPolicyConfig(cloud_route="FRONTIER"))
    with pytest.raises(ValueError):
        DifficultyPrediction(score=float("nan"), layer=10)


def test_hot_swap_preserves_backbone_inflight_version_and_single_prefill(qwen):
    frozen = parameter_hash(qwen.model)
    snapshot = qwen.head_manager.snapshot()
    head = snapshot.heads.isolated_copy()
    with torch.no_grad():
        head.network[-1].bias.fill_(1)
    qwen.head_manager.activate(HeadSnapshot(head, "test-v2"))
    context = qwen.prefill([Message(role="user", content="hello")])
    assert qwen.classify(context, snapshot).score == -1
    assert qwen.classify(context, qwen.head_manager.snapshot()).score == 1
    assert parameter_hash(qwen.model) == frozen
    assert qwen.head_manager.rollback()
    qwen.instrumentation.prefill_calls = qwen.instrumentation.prompt_tokens = 0
    assert (
        service(qwen)
        .run(
            ChatRequest(
                messages=[Message(role="user", content="hello")],
                generation=GenerationOptions(max_new_tokens=2),
            )
        )
        .route
        == "LOCAL"
    )
    assert qwen.instrumentation.prefill_calls == 1
    assert qwen.instrumentation.backbone_instances == 1


def test_actual_trained_artifact_matches_its_training_architecture():
    path = Path(__file__).resolve().parents[2] / "router_heads/out/heads/difficulty.pt"
    snapshot = load_difficulty_head(path)
    assert snapshot.version == "331d32a75ad0a62a"
    assert snapshot.heads.layer == 10 and snapshot.heads.hidden_size == 2048
    raw = torch.load(path, weights_only=True)
    network = torch.nn.Sequential(
        torch.nn.Linear(2048, 256), torch.nn.ReLU(), torch.nn.Dropout(0.1), torch.nn.Linear(256, 1)
    ).eval()
    network.load_state_dict(raw["state_dict"])
    feature = torch.linspace(-1, 1, 2048).unsqueeze(0)
    with torch.inference_mode():
        assert torch.equal(snapshot.heads(feature), network((feature - raw["mean"]) / raw["std"]))


def test_incompatible_heads_rejected_and_previous_preserved(qwen):
    previous = qwen.head_manager.snapshot()
    head = previous.heads.isolated_copy()
    head.layer += 1
    with pytest.raises(ValueError):
        qwen.head_manager.activate(HeadSnapshot(head, "invalid"))
    assert qwen.head_manager.snapshot() is previous
    head = previous.heads.isolated_copy()
    head.std[0] = float("nan")
    with pytest.raises(ValueError):
        qwen.head_manager.activate(HeadSnapshot(head, "invalid"))
    assert qwen.head_manager.snapshot() is previous


def test_api_and_logs_expose_score_not_secrets_or_features(qwen, caplog):
    router = service(qwen)
    with TestClient(create_app(router)) as client:
        response = client.post(
            "/v1/decision",
            json={
                "messages": [{"role": "user", "content": "password=SYNTHETIC_CANARY_DO_NOT_LOG"}]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["difficulty"]["score"] == -1
        assert data["resolved_sensitivity"] == "SENSITIVE" and data["security_flags"]
        assert "probabilities" not in data["difficulty"]
        assert "SYNTHETIC_CANARY" not in json.dumps(data) + caplog.text
        info = client.get("/v1/model/info").json()
        assert info["decision_type"] == "difficulty_regression"
        assert info["backbone_instances"] == 1
    case = EvaluationCase(
        messages=[Message(role="user", content="hello")], difficulty=-1, route="LOCAL"
    )
    outcome = ModelTestHarness(router).run(case, GenerationOptions(max_new_tokens=2))
    report = summarize([case], [outcome], qwen.model_info())
    assert report["difficulty"]["rmse"] == 0
    assert report["complexity"]["examples"] == 0
