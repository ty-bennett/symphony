"""Metric-vs-depth on pilot features: ridge on b_std per candidate layer, val Spearman.
Provisional L_d = shallowest layer within `layer_tolerance` of the best; the top 3 layers go to full extraction.
Writes out/layers.json {candidates, curve_d, L_d_provisional, top3_d, best_d, tolerance}."""
import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from common import CFG, load_tok_data, path, write_json


def shallowest_within(curve, tol):
    """curve: {layer: metric}. Shallowest layer whose metric >= best - tol."""
    best = max(curve.values())
    return min(l for l, v in curve.items() if v >= best - tol)


def ridge_spearman(Xtr, ytr, Xva, yva, alphas):
    sc = StandardScaler().fit(Xtr)
    Xtr, Xva = sc.transform(Xtr), sc.transform(Xva)
    return max(spearmanr(Ridge(alpha=a).fit(Xtr, ytr).predict(Xva), yva)[0] for a in alphas)


def main():
    meta = load_tok_data()[0]
    p = path("out", "pilot")
    layers = np.load(p / "layers.npy").tolist()
    tol = CFG["layer_tolerance"]

    lX, lrows = np.load(p / "last_X.npy", mmap_mode="r"), np.load(p / "last_rows.npy")
    split, b = meta.split.values[lrows], meta.b_std.values[lrows]
    tr, va = split == "train", split == "val"
    curve_d = {}
    for k, l in enumerate(layers):
        X = np.asarray(lX[:, k], dtype=np.float32)
        curve_d[l] = float(ridge_spearman(X[tr], b[tr], X[va], b[va], CFG["train"]["ridge_alphas"]))
        print(f"  L{l:2d} difficulty val Spearman {curve_d[l]:.4f}")

    L_d = shallowest_within(curve_d, tol)
    ranked = sorted(curve_d, key=curve_d.get, reverse=True)
    # keep the provisional pick among the 3 layers carried to full extraction
    top3 = sorted(ranked[:3] if L_d in ranked[:3] else ranked[:2] + [L_d])
    res = dict(candidates=layers, curve_d=curve_d, L_d_provisional=L_d, top3_d=top3, best_d=ranked[0], tolerance=tol)
    write_json(path("out", "layers.json"), res)
    print(f"provisional L_d={L_d} (best {res['best_d']}), top3_d={top3}")


if __name__ == "__main__":
    main()
