"""Read-only readiness checks. Never initialize a model or contact the Hub."""

import json
from pathlib import Path

import torch

from edge_router.config import Settings


def _cached_file(repo: str, name: str, revision: str) -> bool:
    local = Path(repo)
    if local.is_dir():
        return (local / name).is_file()
    from huggingface_hub import try_to_load_from_cache

    cached = try_to_load_from_cache(repo, name, revision=revision)
    return isinstance(cached, str) and Path(cached).is_file()


def readiness(settings: Settings, allow_download: bool = False) -> dict:
    blockers, warnings = [], []
    info = {
        "model_id": settings.model_id,
        "backend": settings.model_backend,
        "device": settings.device,
        "residual_dtype": settings.dtype,
        "quantization": settings.quantization,
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "quantized_artifact_bytes": None,
    }
    if settings.device.startswith("cuda") and not torch.cuda.is_available():
        blockers.append(
            {
                "code": "CUDA_UNAVAILABLE",
                "action": "Use a CUDA/JetPack PyTorch build and GPU, or explicitly select --device cpu.",
            }
        )
    if settings.model_backend == "tiny":
        warnings.append(
            "OFFLINE DEMO only: no meaningful language-model accuracy or Jetson performance."
        )
    elif settings.quantization == "smoothquant_int8":
        directory = Path(settings.quantized_model_path or "")
        required = [
            "manifest.json",
            "quantization.json",
            "model.safetensors",
            "config.json",
            "heads.safetensors",
            "generation_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
        ]
        if not settings.quantized_model_path or any(
            not (directory / name).is_file() for name in required
        ):
            blockers.append(
                {
                    "code": "QUANTIZED_CHECKPOINT_MISSING",
                    "action": "Create/provision an approved SmoothQuant artifact, then set QUANTIZED_MODEL_PATH or --checkpoint.",
                }
            )
        else:
            info["quantized_artifact_bytes"] = sum(
                (directory / name).stat().st_size for name in required
            )
            try:
                path = directory / "quantization.json"
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("Oversized metadata")
                metadata = json.loads(path.read_text())
                if (
                    metadata["identity"]["base_model_id"] != settings.model_id
                    or metadata["residual_dtype"] != settings.dtype
                    or metadata["recipe"]["method"] != "smoothquant_int8"
                ):
                    raise ValueError("Incompatible metadata")
            except (ValueError, KeyError, TypeError):
                blockers.append(
                    {
                        "code": "CHECKPOINT_CONFIG_MISMATCH",
                        "action": "Use the model ID, dtype and artifact that match the approved export.",
                    }
                )
        warnings.append(
            "Readiness checks file presence/config only; loading verifies full artifact integrity. Jetson kernels/quality still require measurement."
        )
    elif not allow_download:
        try:
            weights_cached = any(
                _cached_file(settings.model_id, name, settings.model_revision)
                for name in [
                    "model.safetensors",
                    "model.safetensors.index.json",
                    "pytorch_model.bin",
                    "pytorch_model.bin.index.json",
                ]
            )
            if not weights_cached:
                blockers.append(
                    {
                        "code": "ORIGINAL_MODEL_WEIGHTS_MISSING",
                        "action": "Provision/cache the original weights, or explicitly use --allow-download on a machine with sufficient memory and approved Hub access.",
                    }
                )
            if settings.model_backend == "gemma4_strands" and not (
                _cached_file(
                    settings.strands_checkpoint, "head.safetensors", settings.strands_revision
                )
                and _cached_file(
                    settings.strands_checkpoint,
                    "lora/adapter_model.safetensors",
                    settings.strands_revision,
                )
            ):
                blockers.append(
                    {
                        "code": "STRANDS_WEIGHTS_MISSING",
                        "action": "Provision/cache the matching Strands package or explicitly allow its download.",
                    }
                )
        except (OSError, ValueError):
            blockers.append(
                {
                    "code": "MODEL_CACHE_UNAVAILABLE",
                    "action": "Check the local model paths, Hub cache, and pinned revisions.",
                }
            )
        warnings.append(
            "Offline loading only. Original model and Strands weights must already be cached/provisioned; otherwise loading will fail without downloading."
        )
    else:
        warnings.append(
            "Downloads explicitly allowed: original model weights may be large and require approved Hugging Face access."
        )
    if torch.cuda.is_available() and settings.device.startswith("cuda"):
        try:
            free, total = torch.cuda.mem_get_info(torch.device(settings.device))
            info.update(cuda_free_bytes=free, cuda_total_bytes=total)
            if info["quantized_artifact_bytes"] and info["quantized_artifact_bytes"] > free:
                warnings.append(
                    "Artifact size exceeds currently free CUDA memory; weights/runtime/KV cache may not fit."
                )
        except (RuntimeError, ValueError):
            blockers.append(
                {"code": "CUDA_DEVICE_UNAVAILABLE", "action": "Select an available CUDA device."}
            )
    return {**info, "ready": not blockers, "blockers": blockers, "warnings": warnings}
