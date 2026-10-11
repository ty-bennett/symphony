"""The one main prefill pass, one process per GPU (see run_extract.sh).

Shard = round-robin over prompts sorted by length. Saves to <feats>/shard_i/ (raw appends):
  last_L{l}.f16 + last_rows.i64   last-prompt-token states at the top-3 difficulty layers, every prompt
A shard with done.json is skipped; an unfinished shard is restarted from scratch.
"""
import argparse
import queue
import shutil
import threading
import time

import numpy as np
import torch

from common import (CFG, length_batches, load_tok_data, pad_batch, path, prefill, read_json, restore_quantized,
                    sdpa_backend, shard_rows, text_model, timing, write_json)

E = CFG["extract"]


def writer(q, files):
    """Background thread: wait for each batch's D2H copy, then append to the shard files."""
    while (item := q.get()) is not None:
        ev, arrays = item
        ev.synchronize()
        for name, t in arrays.items():
            t.numpy().tofile(files[name])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--num-shards", type=int, default=E["num_shards"])
    args = ap.parse_args()
    out = path("feats", f"shard_{args.shard}")
    if (out / "done.json").exists():
        print(f"shard {args.shard} already done; skipping")
        return
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    lay = read_json(path("out", "layers.json"))
    layers = lay["top3_d"]
    budget = read_json(path("out", "pilot.json"))["token_budget"]

    meta, toks, offs = load_tok_data()
    lengths = meta.n_tokens.values
    rows = shard_rows(lengths, args.shard, args.num_shards)  # ascending length
    item_ids = meta.item_id.values

    model = restore_quantized(fold=True)
    tm = text_model(model)
    d = model.config.hidden_size
    print(f"shard {args.shard}: {len(rows)} prompts, {lengths[rows].sum():,} tokens, layers {layers}, "
          f"budget {budget}, SDPA {sdpa_backend(model)}")

    names = [f"last_L{l}.f16" for l in layers] + ["last_rows.i64"]
    files = {n: open(out / n, "wb") for n in names}
    q = queue.Queue(maxsize=4)
    th = threading.Thread(target=writer, args=(q, files), daemon=True)
    th.start()

    done_tok = 0
    t0 = time.time()
    # longest batches first, so an OOM shows up immediately
    batches = [rows[b] for b in length_batches(lengths[rows], budget)][::-1]
    with torch.inference_mode():
        for k, r in enumerate(batches):
            s = [np.asarray(toks[offs[i]:offs[i + 1]]) for i in r]
            ids, mask = pad_batch(s, 0)
            h = prefill(tm, layers, ids, mask, prompt_ids=item_ids[r])
            n = torch.as_tensor(lengths[r], device="cuda")
            ar = torch.arange(len(r), device="cuda")
            arrays = {f"last_L{l}.f16": h[l][ar, n - 1].to("cpu", non_blocking=True) for l in layers}
            arrays["last_rows.i64"] = torch.as_tensor(r.astype(np.int64))
            ev = torch.cuda.Event()
            ev.record()
            q.put((ev, arrays))
            done_tok += int(lengths[r].sum())
            if k % E["log_every"] == 0:
                dt = time.time() - t0
                print(f"[shard {args.shard}] batch {k}/{len(batches)} {done_tok:,} tok {done_tok / dt:,.0f} tok/s")
    q.put(None)
    th.join()
    for f in files.values():
        f.close()
    secs = time.time() - t0
    info = dict(shard=args.shard, d_model=d, layers=layers, n_prompts=len(rows), tokens_processed=done_tok,
                seconds=round(secs, 1), tok_s=done_tok / secs,
                peak_gpu_gb=round(torch.cuda.max_memory_allocated() / 1e9, 1),
                bytes=sum((out / n).stat().st_size for n in names))
    write_json(out / "done.json", info)
    timing(f"extract_shard_{args.shard}", **{k: info[k] for k in ("seconds", "tok_s", "tokens_processed", "peak_gpu_gb", "bytes")})
    print(info)


if __name__ == "__main__":
    main()
