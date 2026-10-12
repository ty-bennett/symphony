"""Stage B pilot (one GPU): restore + fold, fold check, early-stop check, throughput/budget tuning,
pilot features at ALL candidate layers for sweep.py. Aborts if projected 8-GPU extraction > 30 min (--force)."""
import argparse
import time

import numpy as np
import torch

from common import (CFG, candidate_layers, length_batches, load_base_fp16, load_tok_data, pad_batch, path, prefill,
                    quant_module_counts, restore_quantized, sdpa_backend, text_model, timing,
                    write_json)

E = CFG["extract"]


def seqs(rows, toks, offs):
    return [np.asarray(toks[offs[r]:offs[r + 1]]) for r in rows]


@torch.inference_mode()
def states(tm, layers, s, pad_id):
    ids, mask = pad_batch(s, pad_id)
    return prefill(tm, layers, ids, mask), mask.bool()


def cos_relerr(a, b, mask):
    a, b = a[mask].float(), b[mask].float()
    return (torch.nn.functional.cosine_similarity(a, b, dim=-1).mean().item(),
            ((a - b).norm() / b.norm()).item())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    rng = np.random.default_rng(CFG["seed"])
    meta, toks, offs = load_tok_data()
    pad_id = 0
    report = {}

    # ---- fold check (8 prompts, every candidate layer): base fp16 / fake-quant / folded
    trval = meta[meta.split != "test"]
    chk = rng.choice(trval.index[trval.n_tokens < 1024], 8, replace=False)
    base = load_base_fp16()
    L = base.config.num_hidden_layers
    layers = candidate_layers(L)
    print(f"L={L}, candidate layers={layers}")
    h_base, mask = states(text_model(base), layers, seqs(chk, toks, offs), pad_id)
    h_base = {l: v.clone() for l, v in h_base.items()}
    del base
    torch.cuda.empty_cache()

    model = restore_quantized(fold=False)
    q, u = quant_module_counts(model)
    print(f"ModelOpt quant config: {q} quantized linears, {u} unquantized")
    h_fq, _ = states(text_model(model), layers, seqs(chk, toks, offs), pad_id)
    h_fq = {l: v.clone() for l, v in h_fq.items()}
    import modelopt.torch.quantization as mtq
    mtq.fold_weight(model)
    tm = text_model(model)
    h_fold, _ = states(tm, layers, seqs(chk, toks, offs), pad_id)
    fold = {}
    for l in layers:
        c_fq, e_fq = cos_relerr(h_fold[l], h_fq[l], mask)
        c_b, e_b = cos_relerr(h_fold[l], h_base[l], mask)
        fold[l] = dict(cos_vs_fakequant=c_fq, relerr_vs_fakequant=e_fq, cos_vs_base=c_b, relerr_vs_base=e_b)
        print(f"  L{l:2d} folded~fakequant cos={c_fq:.6f} relerr={e_fq:.2e} | folded~base cos={c_b:.6f} relerr={e_b:.2e}")
    deep = fold[layers[-1]]
    ok = (all(v["cos_vs_fakequant"] > 0.999 for v in fold.values()) and deep["cos_vs_base"] < 1.0
          and deep["relerr_vs_base"] > 10 * max(deep["relerr_vs_fakequant"], 1e-6))
    report |= dict(quantized_linears=q, unquantized_linears=u, fold_check=fold, fold_check_passed=ok)
    if not ok:
        write_json(path("out", "pilot.json"), report)
        raise SystemExit("ABORT: fold check failed (folded != fake-quant, or quantization not applied)")
    del h_base, h_fq, h_fold

    # ---- early-stopped hooked forward == full output_hidden_states forward
    s = seqs(chk[:4], toks, offs)
    ids, m = pad_batch(s, pad_id)
    with torch.inference_mode():
        h_hook = prefill(tm, layers, ids, m)
        full = tm(input_ids=ids, attention_mask=m, use_cache=False, output_hidden_states=True).hidden_states
    es = {l: cos_relerr(h_hook[l], full[l + 1], m.bool()) for l in layers}
    ok = all(c > 0.99999 and e < 1e-3 for c, e in es.values())
    print(f"early-stop check: {'ok' if ok else 'FAILED'} (min cos {min(c for c, _ in es.values()):.7f})")
    report |= dict(early_stop_check=es, early_stop_check_passed=ok)
    if not ok:
        write_json(path("out", "pilot.json"), report)
        raise SystemExit("ABORT: early-stopped forward differs from full forward")
    report["sdpa_backend"] = sdpa_backend(model)
    print(f"SDPA backend: {report['sdpa_backend']}")

    # ---- throughput: per token budget, on a length-representative sample, to the deepest candidate layer
    sample = np.sort(rng.choice(len(meta), min(4000, len(meta)), replace=False))
    sample = sample[np.argsort(meta.n_tokens.values[sample], kind="stable")]
    tput = {}
    for budget in E["pilot_budgets"]:
        try:
            torch.cuda.synchronize()
            tt, ntok = time.time(), 0
            for bi in length_batches(meta.n_tokens.values[sample], budget)[::-1][:40]:
                states(tm, layers, seqs(sample[bi], toks, offs), pad_id)
                ntok += int(meta.n_tokens.values[sample[bi]].sum())
            torch.cuda.synchronize()
            tput[budget] = ntok / (time.time() - tt)
        except torch.OutOfMemoryError:
            tput[budget] = 0.0
        torch.cuda.empty_cache()
        print(f"  budget {budget}: {tput[budget]:,.0f} tok/s")
    budget = max(tput, key=tput.get)
    tok_s = tput[budget]
    total = int(meta.n_tokens.sum())
    proj_min = total / (tok_s * E["num_shards"]) / 60
    print(f"chosen budget {budget}: {tok_s:,.0f} tok/s/GPU -> projected extract {proj_min:.1f} min on "
          f"{E['num_shards']} GPUs for {total:,} tokens")
    report |= dict(token_budget=int(budget), tok_s_per_gpu=tok_s, throughput=tput, total_tokens=total,
                   projected_extract_min=proj_min)

    # ---- pilot features: last-prompt-token states at all candidate layers (train/val only)
    rows = rng.choice(trval.index.values, min(E["pilot_prompts"], len(trval)), replace=False)
    rows = rows[np.argsort(meta.n_tokens.values[rows], kind="stable")]
    last = []
    tp = time.time()
    for bi in length_batches(meta.n_tokens.values[rows], budget):
        r = rows[bi]
        h, _ = states(tm, layers, seqs(r, toks, offs), pad_id)
        n = torch.as_tensor(meta.n_tokens.values[r], device="cuda")
        ar = torch.arange(len(r), device="cuda")
        last.append(torch.stack([h[l][ar, n - 1] for l in layers], 1).cpu())  # [B, nL, d]
    timing("pilot_features", seconds=round(time.time() - tp, 1))
    out = path("out", "pilot")
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "layers.npy", np.array(layers))
    np.save(out / "last_X.npy", torch.cat(last).numpy())
    np.save(out / "last_rows.npy", rows)
    report["peak_gpu_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 1)
    write_json(path("out", "pilot.json"), report)
    timing("pilot", seconds=round(time.time() - t0, 1), tok_s_per_gpu=round(tok_s), peak_gpu_gb=report["peak_gpu_gb"])
    if proj_min > E["max_extract_minutes"] and not args.force:
        raise SystemExit(f"ABORT: projected extraction {proj_min:.1f} min > {E['max_extract_minutes']} (use --force)")


if __name__ == "__main__":
    main()
