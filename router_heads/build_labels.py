"""Stage A1 (CPU): LLMRouterBench -> unique items, stratified splits, 2PL IRT difficulty labels.

Writes data/items.parquet {item_id, prompt, dataset, split, b_raw, a, b_std, n_obs},
data/responses.parquet {item_id, model, correct}, data/irt.json (theta per model, b mean/std, pools),
data/calib.json (AWQ calibration prompts, train split only).
"""
import glob
import hashlib
import json
import os
import unicodedata

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from common import CFG, path, write_json


def norm_query(q):
    return " ".join(unicodedata.normalize("NFKC", str(q)).split()).lower()


def item_id(origin_query):
    """Same question in both pools -> same id."""
    return hashlib.sha1(norm_query(origin_query).encode()).hexdigest()[:16]


def fit_irt(m, i, y, n_models, n_items, theta=None, steps=3000, lr=0.05):
    """2PL MAP fit: P(correct) = sigmoid(a_i (theta_m - b_i)); theta, b ~ N(0,1), log a ~ N(0,1).
    Full-batch Adam on all observations. Pass `theta` to hold model abilities fixed."""
    m, i, y = torch.tensor(m), torch.tensor(i), torch.tensor(y, dtype=torch.float32)
    fixed = theta is not None
    th = torch.as_tensor(theta, dtype=torch.float32) if fixed else torch.zeros(n_models, requires_grad=True)
    b = torch.zeros(n_items, requires_grad=True)
    log_a = torch.zeros(n_items, requires_grad=True)
    opt = torch.optim.Adam([b, log_a] + ([] if fixed else [th]), lr=lr)
    for _ in range(steps):
        opt.zero_grad()
        logit = log_a.exp()[i] * (th[m] - b[i])
        nll = F.binary_cross_entropy_with_logits(logit, y, reduction="sum")
        prior = 0.5 * (b ** 2).sum() + 0.5 * (log_a ** 2).sum() + (0 if fixed else 0.5 * (th ** 2).sum())
        ((nll + prior) / len(y)).backward()
        opt.step()
    return th.detach().numpy(), b.detach().numpy(), log_a.exp().detach().numpy()


def load_records(root, exclude):
    """One row per (file, record): dataset, model, item_id, prompt, score (NaN = missing)."""
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True)):
        d = json.load(open(f))
        if d["model_name"] in exclude:
            continue
        ds = os.path.relpath(f, root).split(os.sep)[0]
        for r in d["records"]:
            try:
                s = float(r["score"])
            except (TypeError, ValueError):
                s = np.nan
            rows.append((ds, d["model_name"], item_id(r["origin_query"]), r["prompt"], s))
    return pd.DataFrame(rows, columns=["dataset", "model", "item_id", "prompt", "score"])


def split_items(items, fracs, seed):
    """80/10/10 by item id, stratified by dataset."""
    rng = np.random.default_rng(seed)
    split = pd.Series("train", index=items.index)
    for _, idx in items.groupby("dataset").groups.items():
        idx = rng.permutation(np.asarray(idx))
        n_tr, n_va = int(round(fracs[0] * len(idx))), int(round(fracs[1] * len(idx)))
        split[idx[n_tr:n_tr + n_va]] = "val"
        split[idx[n_tr + n_va:]] = "test"
    return split


def main():
    L = CFG["labels"]
    rec = load_records(str(path("lrb_root")), set(L["exclude_models"]))
    print(f"{len(rec)} records, {rec.model.nunique()} models, {rec.dataset.nunique()} datasets")

    # One item per normalized origin_query: most common prompt text, first dataset (sorted) it appears in.
    items = (rec.groupby("item_id")
             .agg(prompt=("prompt", lambda s: s.value_counts().index[0]), dataset=("dataset", "min"))
             .reset_index())
    items["prompt"] = items.prompt.str.strip()
    print(f"{len(items)} unique items")
    print(items.dataset.value_counts().to_string())

    # Responses: mean score per (item, model) over duplicate files/splits, then binarize. NaN = missing.
    resp = rec.dropna(subset=["score"]).groupby(["item_id", "model"]).score.mean().reset_index()
    resp["correct"] = (resp.score >= L["score_threshold"]).astype(np.int8)
    resp = resp[["item_id", "model", "correct"]]

    # Pools, data-driven: small = models run on math500 (small-only), flagship = models run on hle.
    ds_models = rec.groupby("dataset").model.unique()
    small, flag = set(ds_models.get("math500", [])), set(ds_models.get("hle", []))
    both = small & flag
    item_models = resp.groupby("item_id").model.apply(set)
    shared = int(item_models.apply(lambda s: bool(s & small) and bool(s & flag)).sum())
    print(f"pools: small={len(small)} flagship={len(flag)} in-both={sorted(both)}")
    print(f"shared items (scored by both pools): {shared}")
    if shared < L["min_shared_items"]:
        print("!" * 80 + f"\nWARNING: only {shared} shared items; pools may not share a difficulty scale\n" + "!" * 80)

    items["split"] = split_items(items, L["split_frac"], CFG["seed"]).values
    models = sorted(resp.model.unique())
    m_idx = {m: k for k, m in enumerate(models)}

    # IRT on train items only.
    tr = items[items.split == "train"].reset_index(drop=True)
    tr_idx = {it: k for k, it in enumerate(tr.item_id)}
    r = resp[resp.item_id.isin(tr_idx)]
    theta, b_tr, a_tr = fit_irt(r.model.map(m_idx).values, r.item_id.map(tr_idx).values, r.correct.values,
                                len(models), len(tr), steps=L["irt_steps"], lr=L["irt_lr"])
    # Val/test items: theta held fixed, so test labels never move the scale.
    ho = items[items.split != "train"].reset_index(drop=True)
    ho_idx = {it: k for k, it in enumerate(ho.item_id)}
    r = resp[resp.item_id.isin(ho_idx)]
    _, b_ho, a_ho = fit_irt(r.model.map(m_idx).values, r.item_id.map(ho_idx).values, r.correct.values,
                            len(models), len(ho), theta=theta, steps=L["irt_steps"], lr=L["irt_lr"])

    b_raw = dict(zip(tr.item_id, b_tr)) | dict(zip(ho.item_id, b_ho))
    a = dict(zip(tr.item_id, a_tr)) | dict(zip(ho.item_id, a_ho))
    items["b_raw"], items["a"] = items.item_id.map(b_raw), items.item_id.map(a)
    mu, sd = float(b_tr.mean()), float(b_tr.std())
    items["b_std"] = (items.b_raw - mu) / sd
    items["n_obs"] = items.item_id.map(resp.item_id.value_counts()).fillna(0).astype(int)

    out = path("data")
    out.mkdir(parents=True, exist_ok=True)
    items.to_parquet(out / "items.parquet", index=False)
    resp.to_parquet(out / "responses.parquet", index=False)
    write_json(out / "irt.json", {"b_mean": mu, "b_std": sd, "theta": dict(zip(models, map(float, theta))),
                                  "pools": {"small": sorted(small), "flagship": sorted(flag)},
                                  "shared_items": shared, "n_items": len(items), "n_models": len(models)})
    calib = items[items.split == "train"].sample(CFG["backbone"]["calib_samples"], random_state=CFG["seed"])
    write_json(out / "calib.json", [{"id": f"lrb-{r.item_id}", "text": r.prompt} for r in calib.itertuples()])
    print(items.groupby("split").size().to_string())
    print("theta:", ", ".join(f"{m}={t:+.2f}" for m, t in sorted(zip(models, theta), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
