"""Stage A2 (CPU): chat template, length cap (exclude, never truncate), tokenize every LLMRouterBench item.

Writes data/tok/: tokens.npy (int32, concatenated), offsets.npy (int64, n+1), meta.parquet (one row per kept
prompt; row index = global prompt index used by extraction), summary.json (coverage + storage projection).
"""
import numpy as np
import pandas as pd

from common import CFG, chat, chat_template_hash, load_tokenizer, path, write_json


def tokenize_rows(texts, tok, cap, B=512):
    """Template + tokenize; prompts over `cap` tokens are excluded (keep=False), never truncated."""
    toks, n_tok, keep = [], [], []
    texts = list(texts)
    for i0 in range(0, len(texts), B):
        enc = tok([chat(tok, t) for t in texts[i0:i0 + B]], add_special_tokens=False)
        for ids in enc["input_ids"]:
            n_tok.append(len(ids))
            keep.append(len(ids) <= cap)
            if keep[-1]:
                toks.append(np.asarray(ids, dtype=np.int32))
    return toks, n_tok, keep


def main():
    cap = CFG["device_max_prompt_tokens"]
    tok = load_tokenizer()
    df = pd.read_parquet(path("data", "items.parquet"))[["item_id", "prompt", "dataset", "split", "b_std"]]
    print(f"{len(df)} prompts")
    toks, n_tok, keep = tokenize_rows(df.prompt, tok, cap)
    df["n_tokens"], df["kept"] = n_tok, keep

    cov = df.groupby("dataset").agg(total=("kept", "size"), kept=("kept", "sum"))
    cov["excluded"] = cov.total - cov.kept
    cov["excluded_frac"] = cov.excluded / cov.total
    print(f"\nExcluded by device_max_prompt_tokens={cap}:\n{cov.to_string()}")
    for ds, r in cov.iterrows():
        if r.excluded_frac > 0.5:
            print(f"WARNING: {ds} loses {r.excluded_frac:.0%} of its items to the length cap")

    meta = df[df.kept].drop(columns=["prompt", "kept"]).reset_index(drop=True)
    meta["last_idx"] = meta.n_tokens - 1
    offsets = np.concatenate([[0], np.cumsum(meta.n_tokens.values)]).astype(np.int64)

    from transformers import AutoConfig
    b = CFG["backbone"]
    c = AutoConfig.from_pretrained(b["base_repo"], revision=b["base_revision"])
    d = getattr(c, "text_config", c).hidden_size
    proj = dict(d_model=d, prompts=len(meta), total_tokens=int(meta.n_tokens.sum()),
                last_token_gb=round(len(meta) * 3 * d * 2 / 1e9, 2))  # top-3 layers, fp16
    print("\nProjection:", proj)

    out = path("data", "tok")
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "tokens.npy", np.concatenate(toks))
    np.save(out / "offsets.npy", offsets)
    meta.to_parquet(out / "meta.parquet", index=False)
    write_json(out / "summary.json", dict(projection=proj, chat_template_sha256=chat_template_hash(tok),
                                          coverage=cov.reset_index().to_dict("records")))


if __name__ == "__main__":
    main()
