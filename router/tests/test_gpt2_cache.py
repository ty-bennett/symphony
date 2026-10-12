import torch
from test_gemma_cache import tokenizer_fixture
from transformers import GPT2Config, GPT2LMHeadModel

from edge_router.config import Settings
from edge_router.model.gemma3_backend import HuggingFaceBackend
from edge_router.schemas import GenerationOptions, Message


def test_second_explicit_adapter_reuses_cache():
    config = GPT2Config(
        vocab_size=10,
        n_embd=16,
        n_layer=2,
        n_head=2,
        n_positions=128,
        eos_token_id=1,
        pad_token_id=0,
    )
    model = GPT2LMHeadModel(config)
    backend = HuggingFaceBackend(
        model, tokenizer_fixture(), Settings(model_id="random-gpt2-test"), "gpt2"
    )
    backend.eos_ids.clear()
    context = backend.prefill([Message(role="user", content="hello world")])
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda module, args, kwargs: trace.append(kwargs["input_ids"].shape[1]), with_kwargs=True
    )
    actual = [
        t.token_id
        for t in backend.decode_from_prefill(context, GenerationOptions(max_new_tokens=4))
    ]
    hook.remove()
    assert trace == [1, 1, 1]
    with torch.inference_mode():
        ids = context.input_ids
        expected = []
        for _ in range(4):
            logits = model(
                input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False
            ).logits[:, -1, :]
            token = logits.argmax(-1, keepdim=True)
            expected.append(token.item())
            ids = torch.cat((ids, token), dim=-1)
    assert actual == expected
