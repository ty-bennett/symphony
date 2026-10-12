"""Offline development fixture. Not a quality LLM or a production backend."""

import hashlib
from collections.abc import Iterator
from threading import RLock

import torch
from torch import nn

from edge_router.model.decision_heads import (
    DecisionHeadManager,
    DecisionHeads,
    HeadSnapshot,
    predict,
)
from edge_router.model.sampling import sample_token
from edge_router.model.shared_context import GeneratedToken, Instrumentation, PrefillContext
from edge_router.schemas import GenerationOptions, Message


class TinyBackend:
    def __init__(self, max_context_tokens: int = 2048):
        self.execution_lock = RLock()
        self.instrumentation = Instrumentation()
        self.max_context_tokens = max_context_tokens
        with torch.random.fork_rng():
            torch.manual_seed(7)
            self.backbone = nn.Sequential(nn.Embedding(128, 16), nn.Linear(16, 16), nn.Tanh())
            self.lm_head = nn.Linear(16, 128)
            heads = DecisionHeads(16, width=16, dropout=0)
            # Conservative, reproducible bootstrap for the offline harness.
            for p in heads.parameters():
                nn.init.zeros_(p)
            heads.complexity[-1].bias.data[0] = 8
            heads.sensitivity[-1].bias.data[1] = 8
        self.backbone.eval().requires_grad_(False)
        self.lm_head.eval().requires_grad_(False)
        self.head_manager = DecisionHeadManager(HeadSnapshot(heads, "tiny-bootstrap"))
        self.identity = {
            "base_model_id": "offline-tiny-fixture",
            "base_model_revision": "v1",
            "tokenizer_hash": hashlib.sha256(b"ascii128-v1").hexdigest(),
            "chat_template_hash": hashlib.sha256(b"role:content\\nassistant:-v1").hexdigest(),
            "hidden_size": 16,
        }
        self.forward_trace: list[tuple[str, int]] = []

    def prefill(self, messages: list[Message]) -> PrefillContext:
        text = "\n".join(f"{m.role}:{m.content}" for m in messages) + "\nassistant:"
        ids = torch.tensor([[ord(c) % 128 for c in text]])
        if ids.shape[1] > self.max_context_tokens:
            raise ValueError("Prompt exceeds configured context limit")
        with self.execution_lock, torch.inference_mode():
            hidden = self.backbone(ids)[:, -1, :].clone()
        self.instrumentation.prefill_calls += 1
        self.instrumentation.prompt_tokens += ids.numel()
        self.forward_trace.append(("prefill", ids.numel()))
        return PrefillContext(
            ids, torch.ones_like(ids), hidden, {"length": ids.numel()}, ids.numel()
        )

    def classify(self, context: PrefillContext, snapshot: HeadSnapshot):
        return predict(snapshot, context.final_hidden_state)

    def decode_from_prefill(
        self, context: PrefillContext, options: GenerationOptions
    ) -> Iterator[GeneratedToken]:
        if context.consumed:
            raise RuntimeError("A prefill cache may be consumed only once")
        if context.prompt_length + options.max_new_tokens > self.max_context_tokens:
            raise ValueError("Prompt and generation exceed configured context limit")
        context.consumed = True
        with torch.inference_mode():
            logits = self.lm_head(context.final_hidden_state)
        for index in range(options.max_new_tokens):
            token = sample_token(logits, options)
            value = token.item()
            yield GeneratedToken(value, chr(value) if 32 <= value < 127 else "", value == 0)
            if value == 0 or index == options.max_new_tokens - 1:
                return
            with self.execution_lock, torch.inference_mode():
                logits = self.lm_head(self.backbone(token)[:, -1, :])
            context.past_key_values["length"] += 1
            self.instrumentation.decode_calls += 1
            self.instrumentation.decoded_input_tokens += 1
            self.forward_trace.append(("decode", 1))

    def model_info(self) -> dict:
        return {
            **self.identity,
            "backend": "tiny-development-only",
            "device": "cpu",
            "backbone_instances": 1,
            "active_decision_version": self.head_manager.snapshot().version,
        }
