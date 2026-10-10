"""Real transformer/cache tests with random tiny weights; no model downloads."""

import pytest
import torch
from conftest import fixed_heads
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import Gemma3Config, Gemma3ForCausalLM, Gemma3TextConfig, PreTrainedTokenizerFast

from edge_router.config import Settings
from edge_router.model.gemma3_backend import Gemma3Backend, build_backend
from edge_router.model.memory import cache_bytes
from edge_router.schemas import ChatRequest, GenerationOptions, Message
from edge_router.service.orchestrator import Orchestrator


def tokenizer_fixture():
    vocab = {
        "[PAD]": 0,
        "[EOS]": 1,
        "[UNK]": 2,
        "user": 3,
        "assistant": 4,
        ":": 5,
        "hello": 6,
        "world": 7,
        "system": 8,
        "synthetic": 9,
    }
    tokenizer = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    result = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, pad_token="[PAD]", eos_token="[EOS]", unk_token="[UNK]"
    )
    result.chat_template = (
        "{% for m in messages %}{{ m['role'] + ': ' + m['content'] + '\\n' }}{% endfor %}"
        "{% if add_generation_prompt %}{{ 'assistant: ' }}{% endif %}"
    )
    return result


@pytest.fixture
def gemma():
    with torch.random.fork_rng():
        torch.manual_seed(123)
        config = Gemma3TextConfig(
            vocab_size=10,
            hidden_size=16,
            intermediate_size=32,
            num_hidden_layers=2,
            num_attention_heads=2,
            num_key_value_heads=1,
            head_dim=8,
            max_position_embeddings=128,
            sliding_window=4,
            layer_types=["sliding_attention", "full_attention"],
            final_logit_softcapping=4.0,
            query_pre_attn_scalar=8,
        )
        model = Gemma3ForCausalLM(config)
    backend = Gemma3Backend(
        model, tokenizer_fixture(), Settings(model_id="random-gemma-test", max_context_tokens=128)
    )
    backend.eos_ids.clear()  # Test a fixed decode length regardless of random logits.
    return backend


def test_real_gemma_shared_prefill_and_sliding_cache(gemma, policy):
    gemma.head_manager.activate(fixed_heads(gemma, sensitivity="SENSITIVE"))
    trace = []

    def record(module, args, kwargs):
        trace.append(kwargs["input_ids"].shape[1])
        assert kwargs["output_hidden_states"] is False

    hook = gemma.backbone.register_forward_pre_hook(record, with_kwargs=True)
    service = Orchestrator(gemma, policy)
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="hello world hello world hello")],
            generation={"max_new_tokens": 5},
        )
    )
    hook.remove()
    assert result.route == "LOCAL"
    assert trace == [result.decision.prompt_tokens, 1, 1, 1, 1]
    assert gemma.instrumentation.prefill_calls == 1
    assert gemma.instrumentation.prompt_tokens == result.decision.prompt_tokens
    assert gemma.instrumentation.decoded_input_tokens == 4
    assert gemma.backbone is gemma.model.model


def test_reused_cache_matches_full_prefix_logits_across_window_boundary(gemma):
    messages = [Message(role="user", content="hello world hello world hello world")]
    context = gemma.prefill(messages)
    assert cache_bytes(context.past_key_values) > 0
    with torch.inference_mode():
        full = gemma.model(
            input_ids=context.input_ids,
            attention_mask=context.attention_mask,
            use_cache=False,
            logits_to_keep=1,
        )
        shared = gemma._logits(context.final_hidden_state)
    torch.testing.assert_close(shared, full.logits[:, -1, :], atol=1e-6, rtol=1e-5)
    ids = context.input_ids
    expected = []
    with torch.inference_mode():
        for _ in range(5):
            output = gemma.model(
                input_ids=ids,
                attention_mask=torch.ones_like(ids),
                use_cache=False,
                logits_to_keep=1,
            )
            token = output.logits[:, -1, :].argmax(-1, keepdim=True)
            expected.append(token.item())
            ids = torch.cat((ids, token), dim=-1)
    actual = [
        t.token_id for t in gemma.decode_from_prefill(context, GenerationOptions(max_new_tokens=5))
    ]
    assert actual == expected
    # Modern cache evicts sliding-layer K/V while preserving absolute cache positions.
    assert context.past_key_values.layers[0].keys.shape[-2] <= 4
    assert context.past_key_values.layers[1].keys.shape[-2] == context.prompt_length + 4
    with pytest.raises(RuntimeError):
        list(gemma.decode_from_prefill(context, GenerationOptions(max_new_tokens=1)))


@pytest.mark.parametrize("layout", ["old_multimodal", "new_multimodal", "text"])
def test_text_only_loading_maps_multimodal_keys_without_vision_allocation(gemma, tmp_path, layout):
    from safetensors.torch import save_file

    state = {}
    for key, value in gemma.model.state_dict().items():
        if layout == "old_multimodal":
            key = "language_model." + key
        elif layout == "new_multimodal" and key.startswith("model."):
            key = "model.language_model." + key.removeprefix("model.")
        state[key] = value.clone()
    if layout != "text":
        state["vision_tower.synthetic_unused.weight"] = torch.zeros(1)
        config = Gemma3Config(text_config=gemma.model.config.to_dict())
    else:
        config = gemma.model.config
    config.to_json_file(tmp_path / "config.json")
    save_file(state, str(tmp_path / "model.safetensors"), metadata={"format": "pt"})
    gemma.tokenizer.save_pretrained(tmp_path)
    backend = build_backend(
        Settings(model_id=str(tmp_path), model_backend="gemma3", local_files_only=True)
    )
    assert not hasattr(backend.model, "vision_tower")
    assert backend.backbone is backend.model.model
    for key, value in gemma.model.state_dict().items():
        torch.testing.assert_close(backend.model.state_dict()[key], value)


def test_system_message_format_is_shared(gemma):
    formatted = gemma.format_messages(
        [Message(role="system", content="synthetic"), Message(role="user", content="hello")]
    )
    assert formatted == [{"role": "user", "content": "synthetic\n\nhello"}]


def test_real_gemma_backbone_and_lm_hashes_unchanged_by_federated_training(gemma, examples):
    from edge_router.federation.feature_extractor import FrozenFeatureExtractor
    from edge_router.federation.local_trainer import FederatedLocalTrainer
    from edge_router.federation.models import FederationConfig
    from edge_router.model.memory import parameter_hash

    backbone_before = parameter_hash(gemma.backbone)
    lm_before = parameter_hash(gemma.lm_head)
    heads_before = parameter_hash(gemma.head_manager.snapshot().heads)
    result = FederatedLocalTrainer(FrozenFeatureExtractor(gemma), FederationConfig()).train(
        gemma.head_manager.snapshot(), examples
    )
    assert parameter_hash(gemma.backbone) == backbone_before
    assert parameter_hash(gemma.lm_head) == lm_before
    assert parameter_hash(gemma.head_manager.snapshot().heads) == heads_before
    assert parameter_hash(result.candidate.heads) != heads_before


def test_bootstrap_cannot_authorize_frontier(gemma, policy):
    context = gemma.prefill([Message(role="user", content="hello")])
    c, s = gemma.classify(context, gemma.head_manager.snapshot())
    from edge_router.schemas import SecurityContext

    assert policy.decide(c, s, [], SecurityContext()).route != "FRONTIER"


def test_heads_and_first_token_use_exact_same_prefill_representation(gemma):
    context = gemma.prefill([Message(role="user", content="hello world")])
    snapshot = gemma.head_manager.snapshot()
    head_inputs, lm_inputs = [], []
    head_hook = snapshot.heads.register_forward_pre_hook(
        lambda module, args: head_inputs.append(args[0])
    )
    lm_hook = gemma.lm_head.register_forward_pre_hook(
        lambda module, args: lm_inputs.append(args[0])
    )
    gemma.classify(context, snapshot)
    list(gemma.decode_from_prefill(context, GenerationOptions(max_new_tokens=1)))
    head_hook.remove()
    lm_hook.remove()
    assert head_inputs[0] is context.final_hidden_state
    assert lm_inputs[0] is context.final_hidden_state
    assert gemma.instrumentation.prefill_calls == 1


def test_local_training_seed_controls_dropout_without_reseeding_inference(gemma, examples):
    from edge_router.federation.feature_extractor import FrozenFeatureExtractor
    from edge_router.federation.local_trainer import FederatedLocalTrainer
    from edge_router.federation.models import FederationConfig
    from edge_router.model.memory import parameter_hash

    trainer = FederatedLocalTrainer(FrozenFeatureExtractor(gemma), FederationConfig(random_seed=43))
    before = torch.random.get_rng_state().clone()
    first = trainer.train(gemma.head_manager.snapshot(), examples)
    second = trainer.train(gemma.head_manager.snapshot(), examples)
    assert parameter_hash(first.candidate.heads) == parameter_hash(second.candidate.heads)
    assert torch.equal(torch.random.get_rng_state(), before)
