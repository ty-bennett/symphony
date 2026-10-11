"""Explicit dense Gemma 4 calibration targets; no generic model reflection."""

from collections.abc import Iterable

import torch
from torch import nn

from edge_router.model.quantization.smoothquant import SmoothQuantInt8Linear, check_int8_kernel
from edge_router.model.strands_prompt import encode_strands_prompt
from edge_router.schemas import Message


def gemma4_linear_targets(model) -> dict[str, nn.Module]:
    from transformers import Gemma4ForCausalLM

    if not isinstance(model, Gemma4ForCausalLM) or model.config.enable_moe_block:
        raise ValueError("SmoothQuant adapter supports only dense text-only Gemma 4")
    decoder = model.model
    if hasattr(decoder, "peft_config"):
        raise ValueError("Merge the frozen Strands adapter before SmoothQuant calibration")
    targets = {}
    for index, layer in enumerate(decoder.layers):
        for name in ["q_proj", "k_proj", "v_proj", "o_proj"]:
            module = getattr(layer.self_attn, name, None)
            if module is not None:  # Native K/V-sharing layers omit K/V projections.
                targets[f"model.layers.{index}.self_attn.{name}"] = module
        for name in ["gate_proj", "up_proj", "down_proj"]:
            targets[f"model.layers.{index}.mlp.{name}"] = getattr(layer.mlp, name)
    if not targets or any(
        not isinstance(m, (nn.Linear, SmoothQuantInt8Linear)) for m in targets.values()
    ):
        raise ValueError("Unsupported Gemma 4 linear layout")
    # PLE projections, embeddings, norms, attention BMMs, and LM head stay FP16.
    return targets


@torch.inference_mode()
def quantize_backend(
    backend, examples: Iterable[list[Message]], alpha: float = 0.5, max_examples: int = 128
) -> dict:
    """Offline-only conversion. Never call this on a live inference backend.

    One existing model is merged in place, calibrated, then its linear modules
    are replaced. No optimization, labels, gradients, responses, or second LLM.
    Hooks retain only local channel maxima, never full activations or prompts.
    """
    if backend.settings.quantization != "none" or backend.instrumentation.prefill_calls:
        raise ValueError("SmoothQuant conversion requires a fresh, unquantized offline backend")
    if max_examples < 1 or not 0 <= alpha <= 1:
        raise ValueError("Invalid SmoothQuant calibration configuration")
    check_int8_kernel(backend.device)
    if not hasattr(backend.model.model, "merge_and_unload"):
        raise ValueError("Expected the full frozen Strands LoRA before conversion")
    backend.model.model = backend.model.model.merge_and_unload(safe_merge=True)
    backend.backbone = backend.model.model
    backend.model.eval().requires_grad_(False)
    targets = gemma4_linear_targets(backend.model)
    maxima = {}

    def observe(name, module, args):
        values = args[0].detach().reshape(-1, module.in_features).float()
        channel_max = values.abs().amax(dim=0)
        if not torch.isfinite(channel_max).all():
            raise ValueError("Non-finite calibration activation")
        maxima[name] = torch.maximum(maxima[name], channel_max) if name in maxima else channel_max

    handles = [
        module.register_forward_pre_hook(lambda m, args, name=name: observe(name, m, args))
        for name, module in targets.items()
    ]
    count = 0
    try:
        for messages in examples:
            encoded = encode_strands_prompt(backend.tokenizer, messages, backend.requirements)
            ids = encoded.input_ids.to(backend.device)
            limit = min(
                backend.settings.max_context_tokens, backend.model.config.max_position_embeddings
            )
            if ids.shape[1] > limit:
                raise ValueError("Calibration prompt exceeds context limit; truncation forbidden")
            backend.backbone(
                input_ids=ids,
                attention_mask=encoded.attention_mask.to(backend.device),
                use_cache=False,
                output_hidden_states=False,
                output_attentions=False,
            )
            count += 1
            if count == max_examples:
                break
    finally:
        for handle in handles:
            handle.remove()
    if not count or set(maxima) != set(targets):
        raise ValueError("Calibration requires examples covering every quantized linear")
    before = sum(m.weight.numel() * m.weight.element_size() for m in targets.values())
    model_before = sum(v.numel() * v.element_size() for v in backend.model.parameters())
    for name, module in targets.items():
        backend.model.set_submodule(
            name, SmoothQuantInt8Linear.from_float(module, maxima[name], alpha)
        )
    # Release originals before returning. Only the converted backbone is served.
    del targets
    maxima.clear()
    modules = gemma4_linear_targets(backend.model)
    after = sum(v.numel() * v.element_size() for m in modules.values() for v in m.buffers())
    model_after = sum(v.numel() * v.element_size() for v in backend.model.parameters()) + after
    return {
        "method": "smoothquant_int8",
        "schema_version": 1,
        "alpha": alpha,
        "calibration_examples": count,
        "weight_bits": 8,
        "activation_bits": 8,
        "activation_scaling": "dynamic_per_token",
        "weight_scaling": "per_output_channel",
        "kernel": "torch._int_mm",
        "attention_bmm_quantized": False,
        "kv_cache_quantized": False,
        "lora_merged": True,
        "linear_weight_bytes_before": before,
        "linear_storage_bytes_after": after,
        "model_weight_and_quantization_bytes_before": model_before,
        "model_weight_and_quantization_bytes_after": model_after,
        "linear_modules": list(modules),
    }
