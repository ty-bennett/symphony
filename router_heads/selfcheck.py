"""CPU self-checks (run in Stage A). GPU checks (fold check, early-stop check) run inside pilot.py; the ONNX
check on real cached features runs inside export_onnx.py (here it runs on a random head/features).
Usage: python selfcheck.py [name ...]"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from common import CFG, path, shard_rows


def check_irt():
    """2PL fit recovers known a, b, theta (corr > 0.95)."""
    from build_labels import fit_irt
    rng = np.random.default_rng(0)
    nm, ni = 600, 600  # enough responses per item for the discrimination a to be identifiable
    th, b, a = rng.normal(size=nm), rng.normal(size=ni), np.exp(rng.normal(0, 0.5, size=ni))
    m, i = np.meshgrid(np.arange(nm), np.arange(ni), indexing="ij")
    m, i = m.ravel(), i.ravel()
    obs = rng.random(len(m)) < 0.7  # missing pairs stay missing
    m, i = m[obs], i[obs]
    y = (rng.random(len(m)) < 1 / (1 + np.exp(-a[i] * (th[m] - b[i])))).astype(np.float32)
    th_h, b_h, a_h = fit_irt(m, i, y, nm, ni, steps=2000, lr=0.05)
    c = [np.corrcoef(x, xh)[0, 1] for x, xh in ((th, th_h), (b, b_h), (a, a_h))]
    print(f"  corr theta={c[0]:.3f} b={c[1]:.3f} a={c[2]:.3f}")
    assert min(c) > 0.95, c
    # fixed-theta path (val/test items) recovers b too
    _, b_f, _ = fit_irt(m, i, y, nm, ni, theta=th_h, steps=2000)
    assert np.corrcoef(b, b_f)[0, 1] > 0.95


def check_dedup():
    """Identical origin_query (modulo whitespace/case) in both pools -> one item."""
    from build_labels import load_records
    with tempfile.TemporaryDirectory() as d:
        q = "What is  the capital of France?\n"
        for ds, model, query in (("aime", "Qwen3-8B", q), ("aime", "gpt-5", " what is the capital of  france? "),
                                 ("hle", "gpt-5", "A different question")):
            p = Path(d, ds, "test", model)
            p.mkdir(parents=True, exist_ok=True)
            rec = dict(index="1", origin_query=query, prompt=query, score="1.0")
            (p / "x.json").write_text(json.dumps(dict(model_name=model, records=[rec])))
        r = load_records(d, exclude=set())
    assert r.item_id.nunique() == 2 and r.groupby("item_id").model.nunique().max() == 2, r


def check_length_cap():
    """Over-cap prompts are excluded (not truncated) and are still in the IRT fit."""
    from common import load_tokenizer
    from tokenize_all import tokenize_rows
    tok = load_tokenizer()
    texts = ["short prompt", "word " * 300]
    toks, n_tok, keep = tokenize_rows(texts, tok, cap=100)
    assert keep == [True, False] and len(toks) == 1 and len(toks[0]) == n_tok[0] and n_tok[1] > 100
    items_p, meta_p = path("data", "items.parquet"), path("data", "tok", "meta.parquet")
    if items_p.exists() and meta_p.exists():
        items, meta = pd.read_parquet(items_p), pd.read_parquet(meta_p)
        excluded = set(items.item_id) - set(meta.item_id)
        assert items.b_std.notna().all()
        assert meta.n_tokens.max() <= CFG["device_max_prompt_tokens"]
        print(f"  {len(excluded)} over-cap items excluded from features, all with IRT b_std")


def check_sharding():
    """Every prompt in exactly one shard; per-shard token counts within 5%."""
    rng = np.random.default_rng(0)
    cands = [rng.lognormal(5.5, 1.0, 250_000).clip(10, 8192).astype(int)]
    meta_p = path("data", "tok", "meta.parquet")
    if meta_p.exists():
        cands.append(pd.read_parquet(meta_p, columns=["n_tokens"]).n_tokens.values)
    for lengths in cands:
        shards = [shard_rows(lengths, i, 8) for i in range(8)]
        allr = np.concatenate(shards)
        assert len(allr) == len(lengths) and len(np.unique(allr)) == len(lengths)
        tok = np.array([lengths[s].sum() for s in shards])
        print(f"  shard tokens min/max = {tok.min() / tok.max():.4f}")
        assert tok.min() >= 0.95 * tok.max()


def check_onnx():
    """Folded-standardization ONNX graph matches the PyTorch head under ONNX Runtime (random head)."""
    import export_onnx as ex
    from train import MLP
    d = 512
    X = (torch.randn(100, d, generator=torch.Generator().manual_seed(0)) * 3 + 1).numpy()
    h = dict(d_model=d, state_dict=MLP(d).state_dict(), mean=torch.tensor(X.mean(0)), std=torch.tensor(X.std(0)))
    with tempfile.TemporaryDirectory() as t:
        ex.export(h, X[0], Path(t, "d.onnx"))
        print(f"  max abs err {ex.check(h, X, Path(t, 'd.onnx')):.2e}")


CHECKS = {k[6:]: v for k, v in dict(globals()).items() if k.startswith("check_")}

if __name__ == "__main__":
    names = sys.argv[1:] or list(CHECKS)
    for n in names:
        print(f"[{n}]")
        CHECKS[n]()
        print("  ok")
