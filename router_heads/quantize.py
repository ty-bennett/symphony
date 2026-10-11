"""Stage 0 (one GPU, once): ModelOpt INT4 AWQ with TensorRT Edge-LLM 0.11.0's own recipe.

Uses Edge-LLM's `build_quant_config("int4_awq")`, its 64-alignment int4 excludes, its calibration loader/loop
(512 samples x 512 tokens, batch 16) and its export path (`export_hf_checkpoint` with AWQ resmoothing skipped
for hybrid models). One deliberate difference: the model is loaded text-only (Qwen3_5ForCausalLM), so the
vision tower is never built and calibration is text-only on our train-split prompts. Edge-LLM lists
Qwen3_5ForCausalLM text checkpoints as supported. MTP draft weights are not exported (no speculative decoding).

Writes <quant>/modelopt_state.pth (mto.save, for training), <quant>/edgellm_ckpt/ (hand-off), manifest.json.
If manifest.json exists: verify file hashes and exit. Never re-quantize.
"""
import json
import shutil
import sys
import time

import torch

from common import (CFG, chat, chat_template_hash, load_base_fp16, load_tokenizer, path, read_json, sha256_file,
                    timing, write_json)


def file_hashes(root):
    return {str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "manifest.json"}


def main():
    qdir = path("quant")
    man_p = qdir / "manifest.json"
    if man_p.exists():
        man = read_json(man_p)
        bad = {k: v for k, v in file_hashes(qdir).items() if man["files"].get(k) != v}
        bad |= {k: "missing" for k in man["files"] if not (qdir / k).exists()}
        if bad:
            sys.exit(f"ABORT: Stage 0 files changed since quantization: {list(bad)[:10]}")
        print(f"manifest exists, {len(man['files'])} file hashes verified; not re-quantizing.")
        return

    import modelopt
    import modelopt.torch.opt as mto
    import modelopt.torch.quantization as mtq
    import tensorrt_edgellm
    from modelopt.torch.export import export_hf_checkpoint
    from tensorrt_edgellm.quantization import quantize as eq
    from tensorrt_edgellm.quantization.quantization_configs import append_quant_cfg_entries, build_quant_config

    t0 = time.time()
    b = CFG["backbone"]
    tok = load_tokenizer()
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = load_base_fp16()

    calib = read_json(path("data", "calib.json"))
    texts = [chat(tok, c["text"]) for c in calib]
    quant_cfg = build_quant_config(b["quantization"])
    # Edge-LLM: int4 GEMM repack needs N%64==0 and K%64==0; misaligned linears (GDN in_proj_a/b) stay fp16.
    excludes = [{"quantizer_name": f"*{n}.weight_quantizer", "enable": False}
                for n, m in model.named_modules()
                if isinstance(m, torch.nn.Linear) and (m.out_features % 64 or m.in_features % 64)]
    append_quant_cfg_entries(quant_cfg, excludes)
    print(f"int4 excludes (not 64-aligned): {len(excludes)}")

    loader = eq._text_calib_dataloader(tok, lambda: iter(texts), batch_size=b["calib_batch_size"],
                                       num_samples=b["calib_samples"], max_length=b["calib_max_length"])
    mtq.quantize(model, quant_cfg, forward_loop=lambda m: eq._calibrate(m, loader))
    mtq.print_quant_summary(model)
    t_q = time.time() - t0

    qdir.mkdir(parents=True, exist_ok=True)
    mto.save(model, str(qdir / "modelopt_state.pth"))  # before export: export may rewrite weights in place

    edir = qdir / "edgellm_ckpt"
    eq._fix_generation_config_for_strict_validate(model)
    eq._normalize_tied_weights_keys(model)
    with torch.inference_mode(), eq._skip_resmooth_for_hybrid(model, b["quantization"]):
        export_hf_checkpoint(model, export_dir=str(edir))
    eq._remove_stale_safetensors_index(str(edir))
    tok.save_pretrained(str(edir))
    from huggingface_hub import hf_hub_download
    shutil.copy2(hf_hub_download(b["base_repo"], "chat_template.jinja", revision=b["base_revision"]),
                 edir / "chat_template.jinja")

    import transformers
    man = dict(base_repo=b["base_repo"], base_revision=b["base_revision"], quantization=b["quantization"],
               text_only=True, versions=dict(modelopt=modelopt.__version__, tensorrt_edgellm=tensorrt_edgellm.__version__,
                                             torch=torch.__version__, transformers=transformers.__version__),
               quant_cfg=json.loads(json.dumps(quant_cfg, default=str)),
               calib=dict(ids=[c["id"] for c in calib], samples=b["calib_samples"], max_length=b["calib_max_length"],
                          batch_size=b["calib_batch_size"]),
               chat_template_sha256=chat_template_hash(tok), files=file_hashes(qdir))
    write_json(man_p, man)
    print(f"manifest sha256: {sha256_file(man_p)}")
    timing("quantize", seconds=round(time.time() - t0, 1), quantize_seconds=round(t_q, 1),
           peak_gpu_gb=round(torch.cuda.max_memory_allocated() / 1e9, 1))


if __name__ == "__main__":
    main()
