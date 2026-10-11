"""Train the difficulty head on extracted features (single GPU). One float out: d (higher = harder).

Final L_d among the top-3 layers (ridge val Spearman, shallowest within tolerance), then ridge vs
MLP (d_model -> 256 -> 1, ReLU, dropout 0.1, MSE) on standardized features.
Saves out/heads/{difficulty.pt, version.json} and out/train_report.json.
"""
import time

import numpy as np
import torch
from scipy.stats import spearmanr

from common import (CFG, chat_template_hash, load_last, load_tok_data, load_tokenizer, path, read_json, sha256_file,
                    sha256_json, timing, write_json)
from sweep import shallowest_within

T = CFG["train"]
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def standardize_stats(X):
    X = torch.as_tensor(np.asarray(X), dtype=torch.float32)
    return X.mean(0), X.std(0).clamp_min(1e-6)


def fit_ridge(Xtr, ytr, Xva, yva, alphas=T["ridge_alphas"]):
    """Closed-form ridge on standardized features; alpha by val Spearman. Returns dict + val preds."""
    mu, sd = standardize_stats(Xtr)
    A = ((torch.as_tensor(np.asarray(Xtr), dtype=torch.float64) - mu.double()) / sd.double()).to(DEV)
    V = ((torch.as_tensor(np.asarray(Xva), dtype=torch.float64) - mu.double()) / sd.double()).to(DEV)
    y = torch.as_tensor(ytr, dtype=torch.float64, device=DEV)
    G, Ay, ym = A.T @ A, A.T @ (y - y.mean()), y.mean()
    best = None
    for a in alphas:
        coef = torch.linalg.solve(G + a * torch.eye(len(G), device=DEV, dtype=G.dtype), Ay)
        pred = (V @ coef + ym).cpu().numpy()
        rho = spearmanr(pred, yva)[0]
        if best is None or rho > best["val_spearman"]:
            best = dict(alpha=a, coef=coef.float().cpu(), intercept=float(ym), mean=mu, std=sd, val_spearman=float(rho))
            best_pred = pred
    return best, best_pred


def ridge_predict(r, X):
    return (((torch.as_tensor(np.asarray(X), dtype=torch.float32) - r["mean"]) / r["std"]) @ r["coef"]
            + r["intercept"]).numpy()


class MLP(torch.nn.Sequential):
    def __init__(self, d, hidden=T["mlp_hidden"], dropout=T["mlp_dropout"]):
        super().__init__(torch.nn.Linear(d, hidden), torch.nn.ReLU(), torch.nn.Dropout(dropout),
                         torch.nn.Linear(hidden, 1))


def fit_mlp(Xtr, ytr, Xva, yva, noise=T["feature_noise_rel_std"], seed=CFG["seed"]):
    """MLP on standardized features, MSE, AdamW, early stopping on val Spearman."""
    torch.manual_seed(seed)
    mu, sd = standardize_stats(Xtr)
    A = ((torch.as_tensor(np.asarray(Xtr), dtype=torch.float32) - mu) / sd).to(DEV)
    V = ((torch.as_tensor(np.asarray(Xva), dtype=torch.float32) - mu) / sd).to(DEV)
    y = torch.as_tensor(ytr, dtype=torch.float32, device=DEV)
    net = MLP(A.shape[1]).to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=T["mlp_lr"], weight_decay=T["mlp_weight_decay"])
    best, bad = (-2.0, None, None), 0
    for _ in range(T["mlp_epochs"]):
        net.train()
        for idx in torch.randperm(len(A), device=DEV).split(256):
            x = A[idx]
            if noise:  # standardized features: per-dimension std is 1
                x = x + noise * torch.randn_like(x)
            loss = torch.nn.functional.mse_loss(net(x).squeeze(-1), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            pred = net(V).squeeze(-1).cpu().numpy()
        rho = spearmanr(pred, yva)[0]
        if rho > best[0]:
            best, bad = (rho, {k: v.clone() for k, v in net.state_dict().items()}, pred), 0
        elif (bad := bad + 1) >= T["mlp_patience"]:
            break
    net.load_state_dict(best[1])
    return dict(net=net.cpu().eval(), mean=mu, std=sd, val_spearman=float(best[0])), best[2]


def mlp_predict(m, X):
    with torch.no_grad():
        return m["net"](((torch.as_tensor(np.asarray(X), dtype=torch.float32) - m["mean"]) / m["std"])).squeeze(-1).numpy()


def version_info(L_d):
    tok = load_tokenizer()
    return dict(manifest_sha256=sha256_file(path("quant", "manifest.json")), chat_template_sha256=chat_template_hash(tok),
                L_d=L_d, device_max_prompt_tokens=CFG["device_max_prompt_tokens"],
                base_repo=CFG["backbone"]["base_repo"], base_revision=CFG["backbone"]["base_revision"])


def main():
    t0 = time.time()
    meta = load_tok_data()[0]
    split = meta.split.values
    lay = read_json(path("out", "layers.json"))
    tol = CFG["layer_tolerance"]
    rep = {}

    # final L_d among the top 3 on full train/val
    curve, feats = {}, {}
    for l in lay["top3_d"]:
        rows, X = load_last(l)
        sp, b = split[rows], meta.b_std.values[rows]
        tr, va = sp == "train", sp == "val"
        r, _ = fit_ridge(X[tr], b[tr], X[va], b[va])
        curve[l] = r["val_spearman"]
        feats[l] = (rows, X)
        print(f"  L{l}: ridge val Spearman {curve[l]:.4f}")
    L_d = shallowest_within(curve, tol)
    rows, X = feats[L_d]
    sp, b = split[rows], meta.b_std.values[rows]
    tr, va = sp == "train", sp == "val"
    ridge, _ = fit_ridge(X[tr], b[tr], X[va], b[va])
    mlp, _ = fit_mlp(X[tr], b[tr], X[va], b[va])
    print(f"L_d={L_d}: ridge val Spearman {ridge['val_spearman']:.4f}, MLP {mlp['val_spearman']:.4f}")
    rep["difficulty"] = dict(curve_top3=curve, L_d=L_d, best_layer=max(curve, key=curve.get),
                             ridge_val_spearman=ridge["val_spearman"], ridge_alpha=ridge["alpha"],
                             mlp_val_spearman=mlp["val_spearman"])

    ver = version_info(L_d)
    hd = path("out", "heads")
    hd.mkdir(parents=True, exist_ok=True)
    torch.save(dict(layer=L_d, state_dict=mlp["net"].state_dict(), mean=mlp["mean"], std=mlp["std"],
                    d_model=X.shape[1], ridge={k: ridge[k] for k in ("coef", "intercept", "mean", "std", "alpha")},
                    version=ver), hd / "difficulty.pt")
    write_json(hd / "version.json", ver | dict(version_id=sha256_json(ver)[:16]))
    write_json(path("out", "train_report.json"), rep)
    timing("train", seconds=round(time.time() - t0, 1),
           peak_gpu_gb=round(torch.cuda.max_memory_allocated() / 1e9, 1) if DEV == "cuda" else 0)


if __name__ == "__main__":
    main()
