"""Reference reader for the approved ModelOpt 0.45 W4A16_AWQ export.

Weights stay packed in memory. A bounded output-row tile is dequantized for a
PyTorch matmul, then discarded. This is NOT a fused INT4 kernel or TensorRT engine.
ModelOpt packs signed nibbles along OUTPUT rows, unlike AWQ GEMM checkpoints.
"""

import hashlib
import json
from pathlib import Path

import torch
from safetensors import safe_open
from torch import nn
from torch.nn import functional as F
from transformers import GenerationConfig, Qwen3_5ForCausalLM, Qwen3_5TextConfig


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_metadata(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Missing or oversized checkpoint metadata")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Checkpoint metadata must be an object")
    return value


def validate_export(root: Path, identity: dict, verify_files: bool = True) -> Path:
    """Accept quant/ (manifest + edgellm_ckpt), not unrelated Hub AWQ weights."""
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("Missing or oversized quantization manifest")
    if file_hash(manifest_path) != identity["manifest_sha256"]:
        raise ValueError("Quantization manifest does not match the trained head")
    manifest = read_metadata(manifest_path)
    if (
        manifest.get("base_repo") != identity["base_repo"]
        or manifest.get("base_revision") != identity["base_revision"]
        or manifest.get("chat_template_sha256") != identity["chat_template_sha256"]
        or manifest.get("quantization") != "int4_awq"
        or manifest.get("text_only") is not True
        or manifest.get("versions", {}).get("modelopt") != "0.45.0"
    ):
        raise ValueError("Incompatible quantized backbone manifest")
    files = manifest.get("files", {})
    required = {
        "edgellm_ckpt/config.json",
        "edgellm_ckpt/hf_quant_config.json",
        "edgellm_ckpt/model.safetensors",
        "edgellm_ckpt/tokenizer.json",
        "edgellm_ckpt/tokenizer_config.json",
        "edgellm_ckpt/chat_template.jinja",
        "edgellm_ckpt/generation_config.json",
    }
    if not required.issubset(files):
        raise ValueError("Manifest is missing required export files")
    # Validate ALL exported files before any tokenizer/config parsing. modelopt_state
    # is a training artifact, not needed by the packed inference reader.
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        if not relative.parts or relative.parts[0] != "edgellm_ckpt":
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Incomplete quantized export")
        if verify_files and file_hash(path) != digest:
            raise ValueError("Quantized checkpoint file hash mismatch")
    return root / "edgellm_ckpt"


class PackedAWQLinear(nn.Module):
    """Signed symmetric groupwise INT4 with FP16 activations, no zero points."""

    def __init__(
        self,
        in_features: int,
        out_features: int,
        group_size: int,
        chunk_rows: int,
        bias: bool = False,
        device: str | torch.device = "meta",
    ):
        super().__init__()
        if out_features % 2 or in_features % group_size or chunk_rows < 2:
            raise ValueError("Unsupported ModelOpt AWQ dimensions")
        self.in_features, self.out_features, self.group_size = in_features, out_features, group_size
        self.chunk_rows = chunk_rows - chunk_rows % 2
        self.register_buffer(
            "weight",
            torch.empty((out_features // 2, in_features), dtype=torch.uint8, device=device),
        )
        self.register_buffer(
            "weight_scale",
            torch.empty(
                (out_features, in_features // group_size), dtype=torch.float32, device=device
            ),
        )
        self.register_buffer(
            "pre_quant_scale", torch.empty(in_features, dtype=torch.float16, device=device)
        )
        self.register_buffer(
            "bias", torch.empty(out_features, dtype=torch.float16, device=device) if bias else None
        )

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        if hidden.requires_grad:
            raise RuntimeError("Backpropagation through the frozen quantized backbone is forbidden")
        scaled = hidden * self.pre_quant_scale.to(hidden.dtype)
        result = torch.empty(
            (*hidden.shape[:-1], self.out_features), dtype=hidden.dtype, device=hidden.device
        )
        for start in range(0, self.out_features, self.chunk_rows):
            end = min(start + self.chunk_rows, self.out_features)
            packed = self.weight[start // 2 : end // 2]
            nibbles = torch.stack((packed & 15, packed >> 4), dim=1).reshape(
                end - start, self.in_features
            )
            signed = nibbles.to(torch.int8)
            signed = torch.where(signed >= 8, signed - 16, signed)
            groups = signed.reshape(end - start, -1, self.group_size).float()
            weight = (
                (groups * self.weight_scale[start:end, :, None])
                .reshape(end - start, self.in_features)
                .to(hidden.dtype)
            )
            bias = self.bias[start:end].to(hidden.dtype) if self.bias is not None else None
            result[..., start:end] = F.linear(scaled, weight, bias)
        return result


def load_packed_model(directory: Path, device: str, chunk_rows: int) -> Qwen3_5ForCausalLM:
    config_data = read_metadata(directory / "config.json")
    quant = read_metadata(directory / "hf_quant_config.json")
    recipe = quant.get("quantization", {})
    if (
        config_data.get("model_type") != "qwen3_5_text"
        or config_data.get("architectures") != ["Qwen3_5ForCausalLM"]
        or config_data.get("quantization_config", {}).get("quant_method") != "modelopt"
        or quant.get("producer") != {"name": "modelopt", "version": "0.45.0"}
        or recipe.get("quant_algo") != "W4A16_AWQ"
        or recipe.get("group_size") != 128
        or recipe.get("has_zero_point") is not False
        or recipe.get("pre_quant_scale") is not True
        or recipe.get("kv_cache_quant_algo") is not None
    ):
        raise ValueError("Unsupported export: requires ModelOpt 0.45 text-only W4A16_AWQ")
    config_data.pop("quantization_config")  # Loading is explicit, never AutoQuantizer fallback.
    config = Qwen3_5TextConfig(**config_data)
    config._attn_implementation = "sdpa"
    # Meta initialization avoids first allocating another full FP16 backbone.
    with torch.device("meta"):
        model = Qwen3_5ForCausalLM(config)
    excluded = set(recipe.get("exclude_modules", []))
    with safe_open(directory / "model.safetensors", framework="pt", device="cpu") as tensors:
        keys = set(tensors.keys())
        for name, module in list(model.named_modules()):
            if not isinstance(module, nn.Linear) or name in excluded:
                continue
            if f"{name}.weight_scale" not in keys:
                raise ValueError("A quantized linear is missing its AWQ scales")
            model.set_submodule(
                name,
                PackedAWQLinear(
                    module.in_features,
                    module.out_features,
                    128,
                    chunk_rows,
                    module.bias is not None,
                ),
            )
        expected = model.state_dict()
        missing = set(expected) - keys
        # Export omits the tied LM head: share the ONE embedding tensor after load.
        if config.tie_word_embeddings:
            missing.discard("lm_head.weight")
        if missing or keys - set(expected):
            raise ValueError("Export tensor schema does not match the explicit Qwen text model")
        for name in keys:
            value = tensors.get_tensor(name)
            target = expected[name]
            if value.shape != target.shape:
                raise ValueError("Export tensor shape mismatch")
            owner_name, field = name.rsplit(".", 1)
            owner = model.get_submodule(owner_name)
            if isinstance(owner, PackedAWQLinear):
                if value.dtype != target.dtype or not torch.isfinite(value).all():
                    raise ValueError("Invalid packed AWQ tensor dtype or values")
                if field in {"weight_scale", "pre_quant_scale"} and (value <= 0).any():
                    raise ValueError("AWQ scales must be positive")
                owner._buffers[field] = value.to(device)
            elif field in owner._parameters:
                if not value.is_floating_point() or not torch.isfinite(value).all():
                    raise ValueError("Invalid unquantized backbone parameter")
                # Preserve the export's FP16 normal parameters, including recurrent parameters.
                owner._parameters[field] = nn.Parameter(value.to(device), requires_grad=False)
            else:
                owner._buffers[field] = value.to(device)
        del expected
    if config.tie_word_embeddings:
        model.lm_head.weight = model.model.embed_tokens.weight
    # Rotary inverse frequencies are non-persistent buffers: reinitialize without
    # moving/copying already-loaded weights. No meta tensor may remain at runtime.
    from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5TextRotaryEmbedding

    model.model.rotary_emb = Qwen3_5TextRotaryEmbedding(config, device=torch.device(device))
    if any(t.is_meta for t in list(model.parameters()) + list(model.buffers())):
        raise RuntimeError("Incomplete meta model materialization; refusing inference")
    model.generation_config = GenerationConfig.from_pretrained(directory, local_files_only=True)
    return model.eval().requires_grad_(False)
