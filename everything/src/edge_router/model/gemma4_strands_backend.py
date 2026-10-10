"""Gemma 4 + frozen Strands LoRA, with one combined-prompt prefill.

Validated against the installed Transformers 5.18 text model: native DynamicCache
owns sliding-window eviction and intra-forward K/V sharing; explicit position_ids
advance the absolute position. PLE is recomputed for only the supplied tokens.
No separate classifier transformer, cache branches, or adapter switching exist.
"""

import hashlib
import json
import re
from pathlib import Path
from threading import RLock

import torch
import transformers
from huggingface_hub import snapshot_download
from safetensors.torch import load_file

from edge_router.config import Settings
from edge_router.model.decision_heads import DecisionHeadManager, HeadSnapshot, predict
from edge_router.model.gemma3_backend import HuggingFaceBackend, _hash
from edge_router.model.shared_context import Instrumentation, PrefillContext
from edge_router.model.strands_heads import StrandsDecisionHeads
from edge_router.model.strands_prompt import StrandsPromptConfig, encode_strands_prompt
from edge_router.schemas import ComplexityPrediction, Message, SensitivityPrediction


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_strands_artifacts(settings: Settings):
    """Only safetensors + JSON, never remote Python or pickle. Fail closed on mismatch."""
    directory = Path(settings.strands_checkpoint)
    if not directory.is_dir():
        directory = Path(
            snapshot_download(
                settings.strands_checkpoint,
                revision=settings.strands_revision,
                local_files_only=settings.local_files_only,
                allow_patterns=[
                    "strands_decider_config.json",
                    "provenance.json",
                    "head.safetensors",
                    "lora/adapter_config.json",
                    "lora/adapter_model.safetensors",
                ],
            )
        )
    config = json.loads((directory / "strands_decider_config.json").read_text())
    provenance = json.loads((directory / "provenance.json").read_text())
    if (
        config.get("head_type") != "pointer"
        or not config.get("use_lora")
        or config.get("full_weight_targets")
        or config.get("base_model") != settings.model_id
        or provenance.get("base_model") != settings.model_id
    ):
        raise ValueError("Checkpoint is not a matching head-only Strands/Gemma package")
    revision = config.get("base_revision") or provenance.get("base_model_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Strands must identify a pinned base-model commit")
    if settings.model_revision not in {"main", revision}:
        raise ValueError("MODEL_REVISION differs from the trained Strands backbone")
    hashes = {
        "adapter": file_hash(directory / "lora/adapter_model.safetensors"),
        "head": file_hash(directory / "head.safetensors"),
    }
    if any(hashes[k] != provenance.get(k + "_sha256") for k in hashes):
        raise ValueError("Strands artifact digest mismatch")
    adapter = json.loads((directory / "lora/adapter_config.json").read_text())
    allowed = {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}
    if (
        adapter.get("peft_type") != "LORA"
        or adapter.get("modules_to_save")
        or adapter.get("bias", "none") != "none"
        or adapter.get("use_dora", False)
        or not isinstance(adapter.get("target_modules"), list)
        or not set(adapter["target_modules"]).issubset(allowed)
        or set(adapter["target_modules"]) != set(config["lora_targets"])
        or adapter.get("r") != config["lora_r"]
        or adapter.get("lora_alpha") != config["lora_alpha"]
    ):
        raise ValueError("Unsupported Strands adapter; backbone/LM modifications forbidden")
    # Adapter config changes also affect inference; bind it into the frozen identity.
    hashes["adapter_identity"] = _hash({"weights": hashes["adapter"], "config": adapter})
    return directory, config, revision, hashes


class Gemma4StrandsBackend(HuggingFaceBackend):
    """The LM head and both decision readouts consume the SAME final-layer output.

    They use different selected token positions, not different transformer passes.
    The unchanged original conversation is followed by internal questions inside
    the final user turn; the native assistant-generation suffix is last.
    """

    def __init__(
        self,
        model,
        tokenizer,
        settings: Settings,
        heads: StrandsDecisionHeads,
        requirements: StrandsPromptConfig,
        version: str,
        temperature: float = 1.0,
        adapter_hash: str | None = None,
    ):
        from transformers import Gemma4ForCausalLM

        if not transformers.__version__.startswith("5.18."):
            raise RuntimeError("Gemma 4 adapter requires validated Transformers 5.18.x")
        if not isinstance(model, Gemma4ForCausalLM):
            raise TypeError("Gemma 4 backend requires a text-only Gemma4ForCausalLM")
        if model.config.use_bidirectional_attention == "all":
            raise ValueError("Combined-prompt readouts require causal text attention")
        self.model, self.tokenizer, self.settings = model, tokenizer, settings
        self.kind = "gemma4_strands"
        self.backbone, self.lm_head = model.model, model.lm_head
        self.model.eval().requires_grad_(False)
        self.device = next(model.parameters()).device
        self.hidden_size = model.config.hidden_size
        if heads.hidden_size != self.hidden_size:
            raise ValueError("Strands head hidden size does not match Gemma")
        self.requirements = requirements.model_copy(deep=True)
        self.execution_lock, self.instrumentation = RLock(), Instrumentation()
        heads.to(device=self.device, dtype=torch.float32)
        self.head_manager = DecisionHeadManager(
            HeadSnapshot(heads, version, temperature, temperature)
        )
        self.identity = {
            "base_model_id": settings.model_id,
            "base_model_revision": getattr(model.config, "_commit_hash", None)
            or settings.model_revision,
            "tokenizer_hash": _hash(
                {
                    "tokenizer": tokenizer.backend_tokenizer.to_str(),
                    "special": tokenizer.special_tokens_map,
                }
            ),
            "chat_template_hash": _hash(
                {
                    "template": tokenizer.chat_template,
                    "format": "strands-appended-two-choice-v1-no-thinking",
                    "requirements": requirements.model_dump(),
                    "adapter": adapter_hash,
                }
            ),
            "hidden_size": self.hidden_size,
            "head_architecture_version": heads.architecture_version,
            "backbone_adapter_hash": adapter_hash,
            "decision_prompt_hash": _hash(requirements.model_dump()),
        }
        eos = model.generation_config.eos_token_id
        self.eos_ids = set(eos if isinstance(eos, list) else [eos]) - {None}
        if tokenizer.eos_token_id is not None:
            self.eos_ids.add(tokenizer.eos_token_id)

    def _forward(self, ids, mask, cache=None, position: int = 0):
        return self.backbone(
            input_ids=ids,
            attention_mask=mask,
            past_key_values=cache,
            position_ids=torch.arange(
                position, position + ids.shape[1], device=self.device
            ).unsqueeze(0),
            use_cache=True,
            output_hidden_states=False,
            output_attentions=False,
            return_dict=True,
        )

    def prefill(self, messages: list[Message]) -> PrefillContext:
        encoded = encode_strands_prompt(self.tokenizer, messages, self.requirements)
        if (
            self.model.config.bos_token_id is not None
            and encoded.input_ids[0, 0].item() != self.model.config.bos_token_id
        ):
            raise ValueError("Gemma 4 native chat prompt must start with its BOS token")
        ids, mask = encoded.input_ids.to(self.device), encoded.attention_mask.to(self.device)
        limit = min(self.settings.max_context_tokens, self.model.config.max_position_embeddings)
        if ids.shape[0] != 1 or not 0 < ids.shape[1] <= limit:
            raise ValueError("Combined prompt exceeds context limit; truncation is forbidden")
        with self.execution_lock, torch.inference_mode():
            output = self._forward(ids, mask)
            if output.past_key_values is None:
                raise RuntimeError("Gemma 4 did not produce a reusable native cache")
            positions = torch.tensor(encoded.feature_positions, device=self.device)
            # index_select already owns a small allocation, independent of prompt storage.
            features = output.last_hidden_state.index_select(1, positions).reshape(1, -1)
            hidden = output.last_hidden_state[:, -1, :].clone()
        self.instrumentation.prefill_calls += 1
        self.instrumentation.prompt_tokens += ids.numel()
        return PrefillContext(
            ids,
            mask,
            hidden,
            output.past_key_values,
            ids.shape[1],
            {
                "backend_id": id(self),
                "next_cache_position": ids.shape[1],
                "decision_features": features,
                "decision_positions": encoded.feature_positions,
            },
        )

    def classify(
        self, context: PrefillContext, snapshot: HeadSnapshot
    ) -> tuple[ComplexityPrediction, SensitivityPrediction]:
        if context.metadata.get("backend_id") != id(self):
            raise RuntimeError("Classification context belongs to a different backend")
        if not isinstance(snapshot.heads, StrandsDecisionHeads):
            raise TypeError("Gemma 4/Strands cannot classify with an incompatible head")
        return predict(snapshot, context.metadata["decision_features"])

    def model_info(self) -> dict:
        return {
            **super().model_info(),
            "decision_readout": "strands-pointer-two-independent-heads",
            "prompt_format": "appended-two-choice-v1",
            "calibration_scope": "upstream single-question; combined-prompt validation required",
            "adapter_frozen": True,
            "adapter_merged": hasattr(self, "quantization_report"),
            "quantization_details": getattr(self, "quantization_report", None),
        }


def build_gemma4_strands_backend(settings: Settings) -> Gemma4StrandsBackend:
    if not transformers.__version__.startswith("5.18."):
        raise RuntimeError("Install Transformers 5.18.x for Gemma 4")
    if settings.dtype not in {"float32", "float16", "bfloat16"}:
        raise ValueError("Unsupported model dtype")
    if settings.quantization == "smoothquant_int8":
        from edge_router.model.quantization.checkpoint import load_quantized_backend

        return load_quantized_backend(settings)
    if settings.quantization != "none":
        raise NotImplementedError("Gemma 4 quantization requires a validated backend; no fallback")
    from peft import (
        PeftConfig,
        get_peft_model,
        get_peft_model_state_dict,
        set_peft_model_state_dict,
    )
    from transformers import AutoConfig, AutoTokenizer, Gemma4ForCausalLM

    directory, strands, revision, hashes = read_strands_artifacts(settings)
    if settings.max_context_tokens > strands.get("max_length", settings.max_context_tokens):
        raise ValueError("Configured context exceeds the Strands checkpoint's context limit")
    requirements = StrandsPromptConfig.load(settings.strands_prompt_config)
    kwargs = dict(
        revision=revision, local_files_only=settings.local_files_only, trust_remote_code=False
    )
    config = AutoConfig.from_pretrained(settings.model_id, **kwargs)
    if config.model_type != "gemma4" or config.text_config.enable_moe_block:
        raise ValueError("Only dense Gemma 4 E2B/E4B text decoders are supported")
    # Validate the tiny head BEFORE allocating any large Gemma tensors.
    heads = StrandsDecisionHeads(
        config.text_config.hidden_size, strands["pointer_dim"], strands["head_dropout"]
    )
    state = load_file(str(directory / "head.safetensors"))
    expected = heads.complexity.state_dict()
    if set(state) != set(expected) or any(
        value.shape != expected[name].shape or not torch.isfinite(value).all()
        for name, value in state.items()
    ):
        raise ValueError("Strands pointer-head parameters are incompatible or non-finite")
    heads.complexity.load_state_dict(state, strict=True)
    heads.sensitivity.load_state_dict(state, strict=True)
    del state, expected
    temperature = strands.get("temperature_by_kind", {}).get("choice", strands["temperature"])
    if not isinstance(temperature, (int, float)) or not 0 < temperature < float("inf"):
        raise ValueError("Strands choice temperature must be finite and positive")
    model, loading = Gemma4ForCausalLM.from_pretrained(
        settings.model_id,
        config=config.text_config,
        dtype=getattr(torch, settings.dtype),
        attn_implementation="eager",
        output_loading_info=True,
        key_mapping={r"^model\.language_model\.": "model."},
        **kwargs,
    )
    if loading["missing_keys"] or loading.get("mismatched_keys") or loading.get("error_msgs"):
        raise RuntimeError("Incomplete Gemma 4 text weights; refusing random-weight fallback")
    allowed = (
        "model.vision_tower.",
        "model.audio_tower.",
        "model.embed_vision.",
        "model.embed_audio.",
    )
    if any(not k.startswith(allowed) for k in loading["unexpected_keys"]):
        raise RuntimeError("Unexpected Gemma 4 checkpoint layout; update explicit text adapter")
    # Retain the original LM head and attach the adapter to its existing text decoder.
    adapter_config = PeftConfig.from_pretrained(str(directory / "lora"))
    model.model = get_peft_model(model.model, adapter_config)
    adapter_state = load_file(str(directory / "lora/adapter_model.safetensors"))
    expected_adapter = get_peft_model_state_dict(model.model)
    if set(adapter_state) != set(expected_adapter) or any(
        value.shape != expected_adapter[name].shape or not torch.isfinite(value).all()
        for name, value in adapter_state.items()
    ):
        raise ValueError("Incomplete or incompatible LoRA weights; no partial-adapter fallback")
    loaded = set_peft_model_state_dict(model.model, adapter_state)
    if loaded.unexpected_keys:
        raise RuntimeError("Strands adapter weights were not mapped completely")
    del adapter_state, expected_adapter
    model.to(device=settings.device, dtype=getattr(torch, settings.dtype))
    model.config._commit_hash = revision
    tokenizer = AutoTokenizer.from_pretrained(settings.model_id, **kwargs)
    return Gemma4StrandsBackend(
        model,
        tokenizer,
        settings,
        heads,
        requirements,
        f"strands-{hashes['head'][:12]}-{hashes['adapter_identity'][:12]}",
        temperature,
        hashes["adapter_identity"],
    )
