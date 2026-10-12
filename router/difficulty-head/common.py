"""Shared helpers: config, chat template, backbone loading, hooked early-stopped prefill."""
import hashlib
import json
import os
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CFG = yaml.safe_load(open(os.environ.get("ROUTER_CFG", ROOT / "config.yaml")))


def path(key, *parts):
    p = Path(CFG["paths"][key])
    p = p if p.is_absolute() else ROOT / p
    return p.joinpath(*parts)


def sha256_file(p, buf=1 << 24):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def write_json(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, indent=2, default=str))


def read_json(p):
    return json.loads(Path(p).read_text())


def timing(step, **kw):
    """Append one record to the timing report (out/timing.jsonl)."""
    p = path("out", "timing.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"step": step, "time": time.strftime("%F %T"), **kw}) + "\n")


# ---------------------------------------------------------------- tokenizer / chat template
def load_tokenizer():
    from transformers import AutoTokenizer
    b = CFG["backbone"]
    return AutoTokenizer.from_pretrained(b["base_repo"], revision=b["base_revision"])


def chat(tok, text):
    """The exact string the device prefills: user turn + assistant start, thinking off."""
    return tok.apply_chat_template([{"role": "user", "content": text}], tokenize=False,
                                   add_generation_prompt=True,
                                   enable_thinking=CFG["backbone"]["enable_thinking"])


def chat_template_hash(tok):
    return hashlib.sha256(tok.chat_template.encode()).hexdigest()


def candidate_layers(num_layers):
    """Every 2nd decoder layer (0-based index) from L/4 to L-2."""
    return list(range(num_layers // 4, num_layers - 1, 2))


# ---------------------------------------------------------------- backbone (GPU)
def load_base_fp16(device="cuda"):
    """Text-only Qwen3.5 in fp16. AutoModelForCausalLM resolves the VLM checkpoint to
    Qwen3_5ForCausalLM, which never builds the vision tower (model.visual.* keys are ignored)."""
    import torch
    from transformers import AutoModelForCausalLM
    from transformers.models.qwen3_5 import modeling_qwen3_5 as mq
    if not mq.is_fast_path_available:
        raise SystemExit("ABORT: Qwen3.5 GatedDeltaNet fast path unavailable (flash-linear-attention / "
                         "causal-conv1d missing). Refusing the slow torch fallback.")
    b = CFG["backbone"]
    model = AutoModelForCausalLM.from_pretrained(
        b["base_repo"], revision=b["base_revision"], dtype=torch.float16,
        attn_implementation="sdpa").to(device).eval()
    assert type(model).__name__ == "Qwen3_5ForCausalLM", type(model).__name__
    assert not any("visual" in n for n, _ in model.named_modules()), "vision tower was loaded"
    if getattr(model.config, "architectures", None) is None:  # same WAR as Edge-LLM's loader
        model.config.architectures = [type(model).__name__]
    return model


def restore_quantized(fold=True, device="cuda"):
    """Stage 0 checkpoint: base fp16 + ModelOpt restore; optionally fold weight quantizers once.
    INT4 AWQ is weight-only: after folding no fake-quant op remains; the input-side AWQ
    pre_quant_scale stays on input_quantizer exactly as ModelOpt applies it."""
    import modelopt.torch.opt as mto
    import modelopt.torch.quantization as mtq
    model = load_base_fp16(device)
    mto.restore(model, str(path("quant", "modelopt_state.pth")))
    model.eval()  # weights are already fp16; .half() would also cast quantizer amax/pre_quant_scale buffers
    if fold:
        mtq.fold_weight(model)
    return model


def quant_module_counts(model):
    """(quantized linears, unquantized linears) as configured by the ModelOpt quant config.
    Call before fold_weight (folding disables the weight quantizers)."""
    import torch
    q = u = 0
    for _, m in model.named_modules():
        if isinstance(m, torch.nn.Linear):
            wq = getattr(m, "weight_quantizer", None)
            if wq is not None and wq.is_enabled:
                q += 1
            else:
                u += 1
    return q, u


def text_model(model):
    """The language model's base module (no lm_head => no logits)."""
    return model.model


def sdpa_backend(model):
    """Which SDPA kernel PyTorch picks for this model's attention shape with a padding mask."""
    import torch
    c = model.config
    q = torch.randn(2, c.num_attention_heads, 128, c.head_dim, device="cuda", dtype=torch.float16)
    k = torch.randn(2, c.num_key_value_heads, 128, c.head_dim, device="cuda", dtype=torch.float16)
    k = k.repeat_interleave(c.num_attention_heads // c.num_key_value_heads, 1)
    mask = torch.ones(2, 1, 128, 128, device="cuda", dtype=torch.bool).tril()
    try:
        return str(torch.nn.attention.SDPBackend(torch._fused_sdp_choice(q, k, k, mask)))
    except Exception as e:  # private API; only used for logging
        return f"unknown ({e})"


class _Stop(Exception):
    pass


def prefill(tm, layers, input_ids, attention_mask, prompt_ids=None):
    """Run the base text model, capture decoder-layer outputs (residual stream) at `layers`,
    and stop right after the deepest one. Aborts on inf/NaN with layer + prompt id."""
    import torch
    out, deepest = {}, max(layers)

    def mk(l):
        def hook(_m, _i, o):
            out[l] = o[0] if isinstance(o, tuple) else o
            if l == deepest:
                raise _Stop
        return hook

    hs = [tm.layers[l].register_forward_hook(mk(l)) for l in layers]
    try:
        tm(input_ids=input_ids, attention_mask=attention_mask, use_cache=False)
    except _Stop:
        pass
    finally:
        for h in hs:
            h.remove()
    valid = attention_mask.bool()
    bad = torch.stack([(~torch.isfinite(out[l]).all(-1) & valid).any(-1) for l in layers])  # [nL, B]
    if bad.any():
        li, bi = [int(x) for x in bad.nonzero()[0]]
        pid = prompt_ids[bi] if prompt_ids is not None else bi
        raise SystemExit(f"ABORT: inf/NaN in hidden states at layer {layers[li]}, prompt id {pid}")
    return out


def length_batches(lengths, budget):
    """Indices (already sorted by length) grouped so batch_size * max_len <= budget."""
    batches, cur, cur_max = [], [], 0
    for i, n in enumerate(lengths):
        m = max(cur_max, n)
        if cur and m * (len(cur) + 1) > budget:
            batches.append(cur)
            cur, m = [], n
        cur.append(i)
        cur_max = m
    if cur:
        batches.append(cur)
    return batches


def pad_batch(seqs, pad_id, device="cuda"):
    """Right-padded input_ids + attention_mask from a list of int arrays."""
    import torch
    T = max(len(s) for s in seqs)
    ids = torch.full((len(seqs), T), pad_id, dtype=torch.long)
    mask = torch.zeros((len(seqs), T), dtype=torch.long)
    for i, s in enumerate(seqs):
        ids[i, :len(s)] = torch.as_tensor(s, dtype=torch.long)
        mask[i, :len(s)] = 1
    return ids.to(device, non_blocking=True), mask.to(device, non_blocking=True)


def load_tok_data():
    """Stage A tokenized arrays: meta DataFrame + flat token array with offsets."""
    import numpy as np
    import pandas as pd
    d = path("data", "tok")
    return (pd.read_parquet(d / "meta.parquet"), np.load(d / "tokens.npy", mmap_mode="r"),
            np.load(d / "offsets.npy"))


def shard_rows(lengths, shard, num_shards):
    """Round-robin over prompts sorted by length => near-equal token counts per shard."""
    import numpy as np
    order = np.argsort(np.asarray(lengths), kind="stable")
    return order[shard::num_shards]


# ---------------------------------------------------------------- extracted features (written by extract.py)
def shard_dirs():
    n = CFG["extract"]["num_shards"]
    dirs = [path("feats", f"shard_{i}") for i in range(n)]
    missing = [str(d) for d in dirs if not (d / "done.json").exists()]
    if missing:
        raise SystemExit(f"unfinished shards: {missing}")
    return dirs


def load_last(layer):
    """Last-prompt-token states at `layer` for every prompt: (rows, X float16 [n, d])."""
    import numpy as np
    rows, xs = [], []
    for d in shard_dirs():
        done = read_json(d / "done.json")
        rows.append(np.fromfile(d / "last_rows.i64", dtype=np.int64))
        xs.append(np.fromfile(d / f"last_L{layer}.f16", dtype=np.float16).reshape(-1, done["d_model"]))
    return np.concatenate(rows), np.concatenate(xs)
