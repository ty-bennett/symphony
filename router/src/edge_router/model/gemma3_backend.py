"""Explicit text-only Gemma 3 adapter, plus a small GPT-2 adapter.

Gemma uses config-aware DynamicCache; masking/sliding-window eviction is owned by
Transformers. We never reinterpret the cache as legacy tuples or crop it ourselves.
"""

import hashlib
import json
from collections.abc import Iterator
from threading import RLock

import torch
import transformers
from transformers import AutoConfig, AutoTokenizer, Gemma3ForCausalLM, GPT2LMHeadModel

from edge_router.config import Settings
from edge_router.model.decision_heads import (
    DecisionHeadManager,
    DecisionHeads,
    HeadSnapshot,
    predict,
)
from edge_router.model.sampling import sample_token
from edge_router.model.shared_context import GeneratedToken, Instrumentation, PrefillContext
from edge_router.schemas import GenerationOptions, Message


class TextOnlyGemma3ForCausalLM(Gemma3ForCausalLM):
    # Gemma's inherited "language_model" prefix triggers automatic prefix stripping
    # on older multimodal checkpoints BEFORE user key mappings. Disable that path.
    base_model_prefix = ""


GEMMA_TEXT_KEY_MAPPING = {
    r"^language_model\.model\.": "model.",
    r"^language_model\.lm_head\.": "lm_head.",
    r"^model\.language_model\.": "model.",
}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class HuggingFaceBackend:
    def __init__(self, model, tokenizer, settings: Settings, kind: str):
        if not transformers.__version__.startswith(("4.57.", "5.18.")):
            raise RuntimeError("This adapter is validated for Transformers 4.57.x and 5.18.x")
        self.model, self.tokenizer, self.settings, self.kind = model, tokenizer, settings, kind
        self.model.eval().requires_grad_(False)
        self.device = next(model.parameters()).device
        self.execution_lock = RLock()
        self.instrumentation = Instrumentation()
        if kind == "gemma3":
            if not isinstance(model, Gemma3ForCausalLM):
                raise TypeError("Gemma adapter requires a text-only Gemma3ForCausalLM")
            self.backbone = model.model  # References the same modules; no duplicate allocation.
            self.hidden_size = model.config.hidden_size
            format_version = "gemma-system-fold-v1"
        elif kind == "gpt2":
            if not isinstance(model, GPT2LMHeadModel):
                raise TypeError("GPT-2 adapter requires GPT2LMHeadModel")
            self.backbone = model.transformer
            self.hidden_size = model.config.n_embd
            format_version = "native-or-gpt2-role-template-v1"
            if tokenizer.chat_template is None:
                tokenizer.chat_template = (
                    "{% for m in messages %}{{ m['role'] + ': ' + m['content'] + '\\n' }}{% endfor %}"
                    "{% if add_generation_prompt %}{{ 'assistant: ' }}{% endif %}"
                )
        else:
            raise ValueError("Unsupported explicit model adapter")
        self.lm_head = model.lm_head
        heads = DecisionHeads(self.hidden_size).to(device=self.device, dtype=torch.float32)
        self.head_manager = DecisionHeadManager(HeadSnapshot(heads, "untrained-bootstrap"))
        revision = getattr(model.config, "_commit_hash", None) or settings.model_revision
        self.identity = {
            "base_model_id": settings.model_id,
            "base_model_revision": revision,
            "tokenizer_hash": _hash(
                {"vocab": tokenizer.get_vocab(), "special": tokenizer.special_tokens_map}
            ),
            "chat_template_hash": _hash(
                {"template": tokenizer.chat_template, "format": format_version}
            ),
            "hidden_size": self.hidden_size,
        }
        eos = model.generation_config.eos_token_id
        self.eos_ids = set(eos if isinstance(eos, list) else [eos]) - {None}
        if tokenizer.eos_token_id is not None:
            self.eos_ids.add(tokenizer.eos_token_id)

    def format_messages(self, messages: list[Message]) -> list[dict]:
        result = [m.model_dump() for m in messages]
        if self.kind == "gemma3":
            if any(m["role"] == "system" for m in result[1:]):
                raise ValueError("Gemma supports a system message only at the beginning")
            if result[0]["role"] == "system":
                system = result.pop(0)["content"]
                if not result or result[0]["role"] != "user":
                    raise ValueError("Gemma system message must precede a user message")
                result[0]["content"] = system + "\n\n" + result[0]["content"]
        return result

    def _forward(self, ids, mask, cache=None, position: int = 0):
        kwargs = dict(
            input_ids=ids,
            attention_mask=mask,
            past_key_values=cache,
            use_cache=True,
            output_hidden_states=False,
            output_attentions=False,
            return_dict=True,
        )
        positions = torch.arange(position, position + ids.shape[1], device=self.device)
        if self.kind == "gemma3" and transformers.__version__.startswith("4."):
            kwargs["cache_position"] = positions
        else:
            kwargs["position_ids"] = positions.unsqueeze(0)
        return self.backbone(**kwargs)

    def prefill(self, messages: list[Message]) -> PrefillContext:
        encoded = self.tokenizer.apply_chat_template(
            self.format_messages(messages),
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        ids = encoded["input_ids"].to(self.device)
        mask = encoded.get("attention_mask", torch.ones_like(ids)).to(self.device)
        limit = min(self.settings.max_context_tokens, self.model.config.max_position_embeddings)
        if ids.shape[0] != 1 or not 0 < ids.shape[1] <= limit:
            raise ValueError("Prompt exceeds configured context limit; truncation is forbidden")
        with self.execution_lock, torch.inference_mode():
            output = self._forward(ids, mask)
            if output.past_key_values is None:
                raise RuntimeError("Backend failed to produce a reusable KV cache")
            # clone avoids retaining the entire [1, prompt_length, hidden_size] output.
            hidden = output.last_hidden_state[:, -1, :].clone()
        self.instrumentation.prefill_calls += 1
        self.instrumentation.prompt_tokens += ids.numel()
        return PrefillContext(
            ids,
            mask,
            hidden,
            output.past_key_values,
            ids.shape[1],
            {"backend_id": id(self), "next_cache_position": ids.shape[1]},
        )

    def classify(self, context: PrefillContext, snapshot: HeadSnapshot):
        complexity, sensitivity = predict(snapshot, context.final_hidden_state)
        if snapshot.version == "untrained-bootstrap":
            from edge_router.schemas import SensitivityPrediction

            # Random heads have no calibrated evidence that a request is PUBLIC.
            sensitivity = SensitivityPrediction(
                label="PUBLIC", confidence=0.5, probabilities={"PUBLIC": 0.5, "SENSITIVE": 0.5}
            )
        return complexity, sensitivity

    def _logits(self, hidden):
        logits = self.lm_head(hidden)
        if (
            self.kind in {"gemma3", "gemma4_strands"}
            and self.model.config.final_logit_softcapping is not None
        ):
            cap = self.model.config.final_logit_softcapping
            logits = (logits / cap).tanh() * cap
        return logits

    def decode_from_prefill(
        self, context: PrefillContext, options: GenerationOptions
    ) -> Iterator[GeneratedToken]:
        if context.consumed or context.metadata.get("backend_id") != id(self):
            raise RuntimeError("Invalid or already consumed prefill cache")
        limit = min(self.settings.max_context_tokens, self.model.config.max_position_embeddings)
        if context.prompt_length + options.max_new_tokens > limit:
            raise ValueError("Prompt and generation exceed configured context limit")
        context.consumed = True
        with torch.inference_mode():
            logits = self._logits(context.final_hidden_state)
        mask, generated, emitted = context.attention_mask, [], ""
        pending = ""
        for index in range(options.max_new_tokens):
            with torch.inference_mode():
                token = sample_token(logits, options)
            value = token.item()
            generated.append(value)
            eos = value in self.eos_ids
            # Decode the cumulative suffix to handle byte/subword boundaries correctly.
            decoded = self.tokenizer.decode(
                generated, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            final = eos or index == options.max_new_tokens - 1
            pending = decoded[len(emitted) :] if decoded.startswith(emitted) else ""
            if not decoded.endswith("\ufffd") or final:
                emitted = decoded
            else:
                pending = ""
            yield GeneratedToken(value, pending, eos)
            if final:
                return
            mask = torch.cat(
                (mask, torch.ones((1, 1), device=self.device, dtype=mask.dtype)), dim=-1
            )
            position = context.prompt_length + index
            with self.execution_lock, torch.inference_mode():
                output = self._forward(token, mask, context.past_key_values, position)
                context.past_key_values = output.past_key_values
                logits = self._logits(output.last_hidden_state[:, -1, :])
            context.metadata["next_cache_position"] = position + 1
            self.instrumentation.decode_calls += 1
            self.instrumentation.decoded_input_tokens += token.numel()

    def model_info(self) -> dict:
        return {
            **self.identity,
            "backend": self.kind,
            "device": str(self.device),
            "dtype": str(next(self.model.parameters()).dtype),
            "backbone_instances": 1,
            "active_decision_version": self.head_manager.snapshot().version,
            "decision_checkpoint_loaded": self.head_manager.snapshot().version
            != "untrained-bootstrap",
            "max_context_tokens": self.settings.max_context_tokens,
            "quantization": self.settings.quantization,
            "transformers_version": transformers.__version__,
        }


class Gemma3Backend(HuggingFaceBackend):
    def __init__(self, model, tokenizer, settings: Settings):
        super().__init__(model, tokenizer, settings, "gemma3")


def build_backend(settings: Settings) -> HuggingFaceBackend:
    if settings.quantization != "none":
        raise NotImplementedError(
            "INT4/Q4/TensorRT require a dedicated backend; no silent fallback"
        )
    if settings.dtype not in {"float32", "float16", "bfloat16"}:
        raise ValueError("Unsupported model dtype")
    config = AutoConfig.from_pretrained(
        settings.model_id,
        revision=settings.model_revision,
        local_files_only=settings.local_files_only,
        trust_remote_code=False,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        settings.model_id,
        revision=settings.model_revision,
        local_files_only=settings.local_files_only,
        trust_remote_code=False,
    )
    if settings.model_backend == "gemma3":
        text_config = config.text_config if config.model_type == "gemma3" else config
        if text_config.model_type != "gemma3_text":
            raise ValueError("MODEL_ID is not a Gemma 3 checkpoint")
        model, loading = TextOnlyGemma3ForCausalLM.from_pretrained(
            settings.model_id,
            config=text_config,
            revision=settings.model_revision,
            dtype=getattr(torch, settings.dtype),
            key_mapping=GEMMA_TEXT_KEY_MAPPING,
            output_loading_info=True,
            local_files_only=settings.local_files_only,
            attn_implementation="sdpa",
            trust_remote_code=False,
        )
        if loading["missing_keys"] or loading.get("mismatched_keys") or loading.get("error_msgs"):
            raise RuntimeError(
                "Gemma text weights did not load completely; refusing random-weight fallback"
            )
        if any(
            "vision_tower" not in k and "multi_modal_projector" not in k
            for k in loading["unexpected_keys"]
        ):
            raise RuntimeError("Unexpected text checkpoint layout; adapter must be updated")
        kind = "gemma3"
    elif settings.model_backend == "gpt2":
        model = GPT2LMHeadModel.from_pretrained(
            settings.model_id,
            config=config,
            revision=settings.model_revision,
            dtype=getattr(torch, settings.dtype),
            local_files_only=settings.local_files_only,
        )
        kind = "gpt2"
    else:
        raise ValueError("MODEL_BACKEND must be gemma3, gpt2, or tiny")
    # No device_map / Accelerate required; exactly one module tree on one device.
    model.to(settings.device)
    if not getattr(model.config, "_commit_hash", None):
        model.config._commit_hash = getattr(config, "_commit_hash", None)
    return HuggingFaceBackend(model, tokenizer, settings, kind)
