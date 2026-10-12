"""Administrator-provisioned INT8 backbone artifacts; not federated head packages.

The serving loader constructs the model on meta, then maps each stored tensor
directly into its final slot. It never loads an FP16 backbone first. SHA256
digests detect corruption, NOT publisher authenticity; provision immutably.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file, save_model
from torch import nn

from edge_router.config import Settings
from edge_router.model.quantization.gemma4 import gemma4_linear_targets
from edge_router.model.quantization.smoothquant import SmoothQuantInt8Linear, check_int8_kernel
from edge_router.model.strands_heads import StrandsDecisionHeads
from edge_router.model.strands_prompt import StrandsPromptConfig


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def quantization_hash(manifest: dict, recipe: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "weights": manifest["model.safetensors"],
                "config": manifest["config.json"],
                "recipe": recipe,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def save_quantized_backend(backend, recipe: dict, destination: str | Path) -> Path:
    """Save one compressed backbone plus tiny heads/tokenizer. Never overwrite."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("Quantized checkpoints are immutable; choose a new directory")
    modules = gemma4_linear_targets(backend.model)
    if recipe.get("method") != "smoothquant_int8" or any(
        not isinstance(m, SmoothQuantInt8Linear) for m in modules.values()
    ):
        raise ValueError("Refusing to export an unquantized model as INT8")
    staging = Path(tempfile.mkdtemp(prefix="smoothquant-", dir=destination.parent))
    backend.model.config.to_json_file(staging / "config.json")
    backend.model.generation_config.save_pretrained(staging)
    backend.tokenizer.save_pretrained(staging)
    save_model(backend.model, str(staging / "model.safetensors"))
    snapshot = backend.head_manager.snapshot()
    save_file(
        {k: v.detach().cpu().contiguous() for k, v in snapshot.heads.state_dict().items()},
        str(staging / "heads.safetensors"),
    )
    metadata = {
        "identity": backend.identity,
        "recipe": recipe,
        "residual_dtype": str(next(backend.model.parameters()).dtype).removeprefix("torch."),
        "head_width": snapshot.heads.width,
        "head_dropout": snapshot.heads.dropout,
        "head_version": snapshot.version,
        "complexity_temperature": snapshot.complexity_temperature,
        "sensitivity_temperature": snapshot.sensitivity_temperature,
        "global_round": snapshot.global_round,
        "requirements": backend.requirements.model_dump(),
    }
    (staging / "quantization.json").write_text(json.dumps(metadata, indent=2, allow_nan=False))
    manifest = {path.name: digest_file(path) for path in staging.iterdir() if path.is_file()}
    (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2))
    os.replace(staging, destination)
    return destination


def _read_manifest(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    required = {
        "model.safetensors",
        "heads.safetensors",
        "config.json",
        "generation_config.json",
        "quantization.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    if not isinstance(manifest, dict) or not required <= set(manifest):
        raise ValueError("Incomplete SmoothQuant artifact")
    for name, digest in manifest.items():
        if Path(name).name != name or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Invalid SmoothQuant manifest")
        if digest_file(directory / name) != digest:
            raise ValueError("SmoothQuant artifact digest mismatch")
    return manifest


def _load_tensor_slots(model, path: Path, device: torch.device) -> None:
    """Load each parameter/buffer, preserving saved tied-weight aliases."""
    expected = model.state_dict()
    with safe_open(str(path), framework="pt", device="cpu") as stream:
        aliases = stream.metadata() or {}
        keys = set(stream.keys())
        if keys | set(aliases) != set(expected) or any(
            source not in keys for source in aliases.values()
        ):
            raise ValueError("Incomplete or unexpected quantized model tensors")
        for name in sorted(keys):
            value = stream.get_tensor(name)
            if value.shape != expected[name].shape or value.dtype != expected[name].dtype:
                raise ValueError("Invalid quantized tensor shape or precision")
            if value.is_floating_point():
                flat = value.reshape(-1)
                for start in range(0, flat.numel(), 262144):
                    if not torch.isfinite(flat[start : start + 262144]).all():
                        raise ValueError("Non-finite quantized checkpoint tensor")
                if name.endswith(("input_smoothing", "weight_scale")) and not (value > 0).all():
                    raise ValueError("SmoothQuant scales must be positive")
            tensor = value.to(device)
            parent, leaf = name.rsplit(".", 1)
            module = model.get_submodule(parent)
            if leaf in module._parameters:
                tensor = nn.Parameter(tensor, requires_grad=False)
            setattr(module, leaf, tensor)
        for name, source in aliases.items():
            parent, leaf = name.rsplit(".", 1)
            source_parent, source_leaf = source.rsplit(".", 1)
            setattr(
                model.get_submodule(parent),
                leaf,
                getattr(model.get_submodule(source_parent), source_leaf),
            )
    model.tie_weights()


def load_quantized_backend(settings: Settings):
    from transformers import AutoTokenizer, Gemma4ForCausalLM, Gemma4TextConfig, GenerationConfig
    from transformers.models.gemma4.modeling_gemma4 import (
        Gemma4TextRotaryEmbedding,
        Gemma4TextScaledWordEmbedding,
    )

    from edge_router.model.gemma4_strands_backend import Gemma4StrandsBackend

    if not settings.quantized_model_path:
        raise ValueError(
            "QUANTIZED_MODEL_PATH is required for SmoothQuant; run the offline calibration/export command first"
        )
    if settings.dtype not in {"float32", "float16", "bfloat16"}:
        raise ValueError("Unsupported residual dtype for SmoothQuant")
    directory = Path(settings.quantized_model_path)
    manifest = _read_manifest(directory)
    metadata = json.loads((directory / "quantization.json").read_text())
    recipe, identity = metadata["recipe"], metadata["identity"]
    if (
        recipe.get("method") != "smoothquant_int8"
        or recipe.get("schema_version") != 1
        or recipe.get("weight_bits") != 8
        or recipe.get("activation_bits") != 8
        or recipe.get("kernel") != "torch._int_mm"
        or recipe.get("lora_merged") is not True
        or identity["base_model_id"] != settings.model_id
        or settings.model_revision not in {"main", identity["base_model_revision"]}
        or metadata["residual_dtype"] != settings.dtype
    ):
        raise ValueError("Incompatible SmoothQuant checkpoint or dtype")
    requirements = StrandsPromptConfig.load(settings.strands_prompt_config)
    if requirements.model_dump() != metadata["requirements"]:
        raise ValueError("SmoothQuant calibration rubric differs from serving rubric")
    device, dtype = torch.device(settings.device), getattr(torch, settings.dtype)
    check_int8_kernel(device)
    config = Gemma4TextConfig.from_pretrained(directory, local_files_only=True)
    config._attn_implementation = "eager"
    config._commit_hash = identity["base_model_revision"]
    # No random or FP16 production backbone is allocated during loading.
    with torch.device("meta"):
        model = Gemma4ForCausalLM(config).to(dtype=dtype)
        targets = gemma4_linear_targets(model)
        if set(targets) != set(recipe["linear_modules"]):
            raise ValueError("SmoothQuant target modules do not match native Gemma 4")
        for name, module in targets.items():
            model.set_submodule(
                name,
                SmoothQuantInt8Linear(
                    module.in_features, module.out_features, module.bias is not None
                ),
            )
    _load_tensor_slots(model, directory / "model.safetensors", device)
    # Restore native non-persistent buffers, absent from the serialized state.
    with torch.device(device):
        model.model.rotary_emb = Gemma4TextRotaryEmbedding(config)
        for module in model.modules():
            if isinstance(module, Gemma4TextScaledWordEmbedding):
                module.embed_scale = torch.tensor(module.scalar_embed_scale)
    if any(v.is_meta for v in list(model.parameters()) + list(model.buffers())):
        raise RuntimeError("Quantized loader left meta tensors; refusing inference")
    model.generation_config = GenerationConfig.from_pretrained(directory, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(
        directory, local_files_only=True, trust_remote_code=False
    )
    heads = StrandsDecisionHeads(
        config.hidden_size, metadata["head_width"], metadata["head_dropout"]
    )
    weights = load_file(str(directory / "heads.safetensors"))
    expected = heads.state_dict()
    if set(weights) != set(expected) or any(
        weights[k].shape != v.shape
        or weights[k].dtype != torch.float32
        or not torch.isfinite(weights[k]).all()
        for k, v in expected.items()
    ):
        raise ValueError("Invalid quantized artifact decision heads")
    heads.load_state_dict(weights)
    backend = Gemma4StrandsBackend(
        model,
        tokenizer,
        settings,
        heads,
        requirements,
        metadata["head_version"],
        metadata["complexity_temperature"],
        identity["backbone_adapter_hash"],
    )
    if backend.identity != identity:
        raise ValueError("Quantized model tokenizer/backbone/chat-template identity mismatch")
    from edge_router.model.decision_heads import HeadSnapshot

    backend.head_manager.activate(
        HeadSnapshot(
            backend.head_manager.snapshot().heads,
            metadata["head_version"],
            metadata["complexity_temperature"],
            metadata["sensitivity_temperature"],
            metadata["global_round"],
        )
    )
    backend.identity["backbone_quantization_hash"] = quantization_hash(manifest, recipe)
    backend.quantization_report = recipe
    return backend
