"""Evaluation report: difficulty (test + leave-one-dataset-out), layer, coverage, timing.
Writes out/eval_report.json and out/eval_report.md (also printed)."""
import json

import numpy as np
import pandas as pd
import torch
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import roc_auc_score

from common import CFG, load_last, load_tok_data, path, read_json, write_json
from train import MLP, fit_mlp, fit_ridge, mlp_predict, ridge_predict


def reg_metrics(pred, y, ds=None, min_n=10):
    out = dict(n=len(y), spearman=float(spearmanr(pred, y)[0]), pearson=float(pearsonr(pred, y)[0]),
               rmse=float(np.sqrt(np.mean((pred - y) ** 2))))
    if ds is not None:
        per = {d: float(spearmanr(pred[ds == d], y[ds == d])[0]) for d in np.unique(ds) if (ds == d).sum() >= min_n}
        out["within_dataset_spearman_mean"] = float(np.nanmean(list(per.values())))
        out["within_dataset_spearman"] = per
    return out


def difficulty(meta):
    h = torch.load(path("out", "heads", "difficulty.pt"), weights_only=False)
    net = MLP(h["d_model"])
    net.load_state_dict(h["state_dict"])
    mlp = dict(net=net.eval(), mean=h["mean"], std=h["std"])
    rows, X = load_last(h["layer"])
    m = meta.iloc[rows].reset_index(drop=True)
    d_mlp, d_ridge = mlp_predict(mlp, X), ridge_predict(h["ridge"], X)
    te = (m.split == "test").values
    rep = dict(layer=h["layer"],
               test_mlp=reg_metrics(d_mlp[te], m.b_std.values[te], m.dataset.values[te]),
               test_ridge=reg_metrics(d_ridge[te], m.b_std.values[te], m.dataset.values[te]))
    gap = rep["test_mlp"]["spearman"] - rep["test_ridge"]["spearman"]
    rep["mlp_minus_ridge_spearman"] = gap
    rep["mlp_ridge_gap_small"] = abs(gap) < 0.02

    # Does d track real model success? AUC of -d for per-item correctness.
    resp = pd.read_parquet(path("data", "responses.parquet"))
    dt = pd.DataFrame({"item_id": m.item_id[te], "d": d_mlp[te]})
    rep["correctness_auc"] = {}
    for model in CFG["labels"]["auc_models"]:
        r = resp[resp.model == model].merge(dt, on="item_id")
        rep["correctness_auc"][model] = (float(roc_auc_score(r.correct, -r.d)) if r.correct.nunique() == 2
                                         else None) if len(r) else None

    # Leave-one-dataset-out: train on other datasets' train items, test on every item of the held-out one.
    # Caveat: the held-out dataset's train items still helped set the IRT scale (b_std).
    lodo = {}
    for ds in sorted(m.dataset.unique()):
        tr = ((m.split == "train") & (m.dataset != ds)).values
        va = ((m.split == "val") & (m.dataset != ds)).values
        ho = (m.dataset == ds).values
        if ho.sum() < 10:
            continue
        r, _ = fit_ridge(X[tr], m.b_std.values[tr], X[va], m.b_std.values[va])
        n, _ = fit_mlp(X[tr], m.b_std.values[tr], X[va], m.b_std.values[va])
        lodo[ds] = dict(mlp=reg_metrics(mlp_predict(n, X[ho]), m.b_std.values[ho]),
                        ridge=reg_metrics(ridge_predict(r, X[ho]), m.b_std.values[ho]))
        print(f"  LODO {ds}: MLP Spearman {lodo[ds]['mlp']['spearman']:.3f}, ridge {lodo[ds]['ridge']['spearman']:.3f}")
    rep["lodo"] = lodo
    rep["lodo_mean_spearman"] = dict(mlp=float(np.mean([v["mlp"]["spearman"] for v in lodo.values()])),
                                     ridge=float(np.mean([v["ridge"]["spearman"] for v in lodo.values()])))
    return rep


def layer_report():
    from transformers import AutoConfig
    bb = CFG["backbone"]
    c = AutoConfig.from_pretrained(bb["base_repo"], revision=bb["base_revision"])
    L = getattr(c, "text_config", c).num_hidden_layers
    lay, tr = read_json(path("out", "layers.json")), read_json(path("out", "train_report.json"))
    L_d = tr["difficulty"]["L_d"]
    cd = {int(k): v for k, v in tr["difficulty"]["curve_top3"].items()}
    return dict(L=L, L_d=L_d, prefill_fraction=(L_d + 1) / L, pilot_curve_d=lay["curve_d"], final_curve_d_top3=cd,
                cost_d_spearman=max(cd.values()) - cd[L_d], best_d=max(cd, key=cd.get))


def timing_report():
    recs = [json.loads(l) for l in open(path("out", "timing.jsonl"))]
    ext = [r for r in recs if r["step"].startswith("extract_shard")]
    out = {r["step"]: r for r in recs if not r["step"].startswith("extract_shard")}
    if ext:
        tok = sum(r["tokens_processed"] for r in ext)
        wall = max(r["seconds"] for r in ext)
        out["extract"] = dict(wall_seconds=wall, total_tokens=tok, tok_s_total=tok / wall,
                              tok_s_per_gpu=[round(r["tok_s"]) for r in ext], peak_gpu_gb=max(r["peak_gpu_gb"] for r in ext),
                              storage_gb=round(sum(r["bytes"] for r in ext) / 1e9, 1))
    return out


def to_md(rep):
    d = rep["difficulty"]
    f = lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)
    lines = ["# Difficulty head evaluation", "", "## Difficulty (test)", "",
             "| head | Spearman | Pearson | RMSE | within-dataset Spearman |", "|---|---|---|---|---|"]
    for k in ("test_mlp", "test_ridge"):
        m = d[k]
        lines.append(f"| {k[5:]} | {f(m['spearman'])} | {f(m['pearson'])} | {f(m['rmse'])} | {f(m['within_dataset_spearman_mean'])} |")
    lines += ["", f"MLP - ridge Spearman: {f(d['mlp_minus_ridge_spearman'])}"
              + (" (under 0.02: the MLP adds little over ridge)" if d["mlp_ridge_gap_small"] else ""),
              f"Leave-one-dataset-out mean Spearman: MLP {f(d['lodo_mean_spearman']['mlp'])}, "
              f"ridge {f(d['lodo_mean_spearman']['ridge'])}",
              "Correctness AUC from -d: " + ", ".join(f"{k} {f(v)}" for k, v in d["correctness_auc"].items())]
    lines += ["", "## Layers", ""]
    lr = rep["layers"]
    lines += [f"L_d={lr['L_d']} (best {lr['best_d']}, cost {f(lr['cost_d_spearman'])} Spearman), "
              f"prefill needed for routing: {f(lr['prefill_fraction'])} of {lr['L']} layers", "",
              "## Coverage (LLMRouterBench items excluded by the length cap)", ""]
    lines += [f"- {c['dataset']}: {c['excluded']}/{c['total']} ({c['excluded_frac']:.1%})" for c in rep["coverage"]]
    lines += ["", "## Timing", "", "```", json.dumps(rep["timing"], indent=1), "```"]
    return "\n".join(lines)


def main():
    meta = load_tok_data()[0]
    rep = dict(difficulty=difficulty(meta), layers=layer_report(),
               coverage=read_json(path("data", "tok", "summary.json"))["coverage"],
               timing=timing_report())
    write_json(path("out", "eval_report.json"), rep)
    md = to_md(rep)
    path("out", "eval_report.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
