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
        tensors = [getattr(layer, name, None) for name in ["keys", "values"]]
        for name in ["conv_states", "recurrent_states"]:
            states = getattr(layer, name, {})
            tensors.extend(states.values() if isinstance(states, dict) else [states])
        for tensor in tensors:
            if isinstance(tensor, torch.Tensor):
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


def component_memory(modules: dict[str, torch.nn.Module]) -> dict:
    """Count actual parameter/buffer storages, including packed weights and aliases.

    Components are ordered: additional_bytes excludes storage already counted by
    earlier components. A tied LM head thus references weights but adds no bytes.
    This is resident tensor memory, NOT allocator overhead or activation memory.
    No tensors are copied or materialized while inspecting their storages.
    """
    seen, components = set(), {}
    total = 0
    for name, module in modules.items():
        storages = {}
        for tensor in list(module.parameters()) + list(module.buffers()):
            if tensor.is_meta:
                raise ValueError("Cannot measure an unmaterialized model")
            storage = tensor.untyped_storage()
            key = (str(tensor.device), storage.data_ptr())
            storages[key] = storage.nbytes()
        resident = sum(storages.values())
        additional = sum(size for key, size in storages.items() if key not in seen)
        components[name] = {
            "resident_bytes": resident,
            "additional_bytes": additional,
            "shared_with_previous_bytes": resident - additional,
        }
        seen.update(storages)
        total += additional
    return {"components": components, "total_unique_bytes": total}
