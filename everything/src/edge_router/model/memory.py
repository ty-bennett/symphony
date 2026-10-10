import hashlib

import torch


def synchronize():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def cache_bytes(cache) -> int:
    # Cache.layers is the modern Transformers interface; no old tuple assumptions.
    if not hasattr(cache, "layers"):
        return 0
    storages = {}
    for layer in cache.layers:
        for tensor in (layer.keys, layer.values):
            if tensor is not None:
                storage = tensor.untyped_storage()
                storages[storage.data_ptr()] = storage.nbytes()
    return sum(storages.values())


def parameter_hash(module) -> str:
    """Expensive benchmark/test audit, never in request-time code."""
    digest = hashlib.sha256()
    for name, value in module.state_dict().items():
        digest.update(name.encode())
        flat = value.detach().reshape(-1)
        for start in range(0, flat.numel(), 262144):
            chunk = flat[start : start + 262144].cpu().contiguous()
            digest.update(chunk.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()
