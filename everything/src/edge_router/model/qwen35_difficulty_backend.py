"""Explicit text-only Qwen3.5 adapter with a layer-residual regression head.

The full prefill completes all decoder layers exactly once. A temporary hook clones
only the last prompt-token vector at the trained decision layer. Generation uses
the final normalized state and the very same DynamicCache (attention + GDN states).
"""

import hashlib
from pathlib import Path
from threading import RLock

import torch
import transformers
from transformers import AutoTokenizer, Qwen3_5ForCausalLM

from edge_router.config import Settings
from edge_router.model.decision_heads import DecisionHeadManager, HeadSnapshot
from edge_router.model.difficulty_head import (
    DifficultyHead,
    load_difficulty_head,
    predict_difficulty,
)
from edge_router.model.gemma3_backend import HuggingFaceBackend, _hash
from edge_router.model.quantization.modelopt_awq import load_packed_model, validate_export
from edge_router.model.shared_context import Instrumentation, PrefillContext
from edge_router.schemas import Message


class Qwen35DifficultyBackend(HuggingFaceBackend):
    def __init__(self, model, tokenizer, settings: Settings, snapshot: HeadSnapshot):
        if not transformers.__version__.startswith("5.18."):
            raise RuntimeError("Qwen shared-cache adapter is validated for Transformers 5.18.x")
        if not isinstance(model, Qwen3_5ForCausalLM) or not isinstance(
            snapshot.heads, DifficultyHead
        ):
            raise TypeError("Requires a text-only Qwen3.5 backbone and trained difficulty head")
        head = snapshot.heads
        template_hash = hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
        if (
            head.identity["base_repo"] != settings.model_id
            or head.identity["base_revision"] != settings.model_revision
            or head.identity["chat_template_sha256"] != template_hash
            or head.hidden_size != model.config.hidden_size
            or not 0 <= head.layer < len(model.model.layers)
            or settings.max_context_tokens > head.identity["device_max_prompt_tokens"]
        ):
            raise ValueError(
                "Decision head is incompatible with backbone/template/context configuration"
            )
        self.model, self.tokenizer, self.settings = model, tokenizer, settings
        self.kind = "qwen35_difficulty"
        self.model.eval().requires_grad_(False)
        self.device = model.model.embed_tokens.weight.device
        self.backbone, self.lm_head = model.model, model.lm_head
        self.hidden_size = model.config.hidden_size
        self.execution_lock = RLock()
        self.instrumentation = Instrumentation()
        self.head_manager = DecisionHeadManager(
            HeadSnapshot(head.isolated_copy(self.device), snapshot.version)
        )
        self.decision_layer = head.layer
        self.identity = {
            "base_model_id": settings.model_id,
            "base_model_revision": settings.model_revision,
            "tokenizer_hash": _hash(
                {"vocab": tokenizer.get_vocab(), "special": tokenizer.special_tokens_map}
            ),
            "chat_template_hash": template_hash,
            "quantization_manifest_hash": head.identity["manifest_sha256"],
            "hidden_size": self.hidden_size,
        }
        eos = model.generation_config.eos_token_id
        self.eos_ids = set(eos if isinstance(eos, list) else [eos]) - {None}
        if tokenizer.eos_token_id is not None:
            self.eos_ids.add(tokenizer.eos_token_id)

    def format_messages(self, messages: list[Message]) -> list[dict]:
        return [message.model_dump() for message in messages]

    def prefill(self, messages: list[Message]) -> PrefillContext:
        with self.execution_lock, torch.inference_mode():
            encoded = self.tokenizer.apply_chat_template(
                self.format_messages(messages),
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
                return_tensors="pt",
                return_dict=True,
            )
            ids = encoded["input_ids"].to(self.device)
            mask = encoded.get("attention_mask", torch.ones_like(ids)).to(self.device)
            limit = min(self.settings.max_context_tokens, self.model.config.max_position_embeddings)
            if ids.shape[0] != 1 or not 0 < ids.shape[1] <= limit:
                raise ValueError("Prompt exceeds context limit; truncation is forbidden")
            captured = []

            def capture(module, args, output):
                if not isinstance(output, torch.Tensor) or output.shape[-1] != self.hidden_size:
                    raise RuntimeError("Qwen decoder layer output semantics changed")
                captured.append(output[:, -1, :].clone())

            handle = self.backbone.layers[self.decision_layer].register_forward_hook(capture)
            try:
                output = self._forward(ids, mask)
            finally:
                handle.remove()
            if len(captured) != 1 or output.past_key_values is None:
                raise RuntimeError(
                    "A single complete prefill with decision feature/cache is required"
                )
            final = output.last_hidden_state[:, -1, :].clone()
        self.instrumentation.prefill_calls += 1
        self.instrumentation.prompt_tokens += ids.numel()
        return PrefillContext(
            input_ids=ids,
            attention_mask=mask,
            final_hidden_state=final,
            past_key_values=output.past_key_values,
            prompt_length=ids.shape[1],
            metadata={
                "backend_id": id(self),
                "next_cache_position": ids.shape[1],
                "decision_layer": self.decision_layer,
            },
            decision_hidden_state=captured[0],
        )

    def classify(self, context: PrefillContext, snapshot: HeadSnapshot):
        if (
            context.metadata.get("backend_id") != id(self)
            or context.decision_hidden_state is None
            or not isinstance(snapshot.heads, DifficultyHead)
            or snapshot.heads.layer != self.decision_layer
        ):
            raise RuntimeError("Invalid regression feature or head snapshot")
        return predict_difficulty(snapshot, context.decision_hidden_state)

    def model_info(self) -> dict:
        return {
            **super().model_info(),
            "decision_type": "difficulty_regression",
            "decision_layer": self.decision_layer,
            "decision_target": "standardized_irt_difficulty",
            "enable_thinking": False,
            "cloud_destination": "PRIVATE_AWS",
            "cache_type": "Transformers DynamicCache (attention and GDN recurrent states)",
            "quantization_runtime": "packed weights with tiled reference dequantization; not fused TensorRT",
            "weight_precision": "INT4 AWQ linears; FP16 embeddings/LM head and excluded modules",
            "activation_precision": "FP16 with model-defined FP32 accumulations",
        }


def build_qwen35_difficulty_backend(settings: Settings) -> Qwen35DifficultyBackend:
    if settings.quantization != "modelopt_awq_int4" or settings.dtype != "float16":
        raise ValueError("This trained head requires its exact ModelOpt W4A16_AWQ FP16 export")
    if not settings.quantized_model_path:
        raise ValueError(
            "Set QUANTIZED_MODEL_PATH to quant/ from the router_heads README weights link"
        )
    if settings.decision_checkpoint or settings.federation_enabled:
        raise ValueError(
            "Legacy classification checkpoints/training cannot replace the regression head"
        )
    snapshot = load_difficulty_head(settings.difficulty_checkpoint)
    if (
        snapshot.heads.identity["base_repo"] != settings.model_id
        or snapshot.heads.identity["base_revision"] != settings.model_revision
    ):
        raise ValueError("Configured base model/revision does not match the difficulty head")
    directory = validate_export(Path(settings.quantized_model_path), snapshot.heads.identity)
    # All assets are local and hash-checked. No implicit Hub, AWS or Drive requests.
    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
    if (
        hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
        != snapshot.heads.identity["chat_template_sha256"]
    ):
        raise ValueError("Checkpoint chat template differs from training")
    model = load_packed_model(directory, settings.device, settings.awq_dequant_chunk_rows)
    return Qwen35DifficultyBackend(model, tokenizer, settings, snapshot)
