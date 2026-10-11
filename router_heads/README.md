# Router difficulty head on a frozen INT4 AWQ Qwen3.5

Trains one small head on prefill hidden states of a ModelOpt INT4 AWQ `Qwen/Qwen3.5-4B` (text-only, thinking
off), for later deployment on Jetson Orin Nano with TensorRT Edge-LLM. The head has a single float output:

- `d`: task difficulty of the prompt (MLP on the last prompt token at layer L_d; target = standardized 2PL IRT
  difficulty `b_i`; higher = harder). The router compares `d` with one cutoff to choose between the two model
  tiers; the cutoff is not part of the head.

Prefill only. The backbone is quantized once (Stage 0) and never modified afterwards. The vision encoder is never built.

Everything is configured in [config.yaml](config.yaml). To use `Qwen/Qwen3.5-2B`, change `backbone.base_repo` and
`backbone.base_revision`; no code changes are needed.

## Setup

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# GPU node only (no wheel exists for causal-conv1d; build it for sm_100):
TORCH_CUDA_ARCH_LIST="10.0" pip install --no-build-isolation causal-conv1d==1.7.0
```

LLMRouterBench ships as a tarball, not a HF dataset. Fetch it at the pinned revision:

```bash
mkdir -p data/raw && cd data/raw
curl -L -o bench.tgz https://huggingface.co/datasets/NPULH/LLMRouterBench/resolve/0e5af1b84bf73437a01a1849c0f1d2468baa93fc/bench-release.tar.gz
tar xzf bench.tgz   # -> data/raw/bench-release/
```

## Run order

### Stage A: CPU, before renting the GPU node (~6 min)

```bash
python build_labels.py     # items, splits, 2PL IRT, AWQ calibration set -> data/items.parquet, irt.json, calib.json
python tokenize_all.py     # chat template, length cap, coverage, storage projection -> data/tok/
python selfcheck.py        # CPU self-checks
```

Then copy `router_heads/` (including `data/`) to the node.

### Stage B: HGX B200 node

```bash
python quantize.py         # Stage 0, one GPU. Skips and verifies hashes if the manifest exists.
python pilot.py            # fold check, early-stop check, budget tuning, pilot features (aborts if > 30 min projected)
python sweep.py            # metric-vs-depth -> out/layers.json (provisional L_d, top-3 layers)
./run_extract.sh           # 8 independent processes, one per GPU; re-run to resume (finished shards are skipped)
python train.py            # final L_d, MLP (+ ridge baseline) -> out/heads/
python evaluate.py         # -> out/eval_report.md / .json
python export_onnx.py      # -> out/onnx/difficulty.onnx + heads.json sidecar (ONNX Runtime check included)
```

Copy off the node: `out/heads/`, `out/onnx/`, `<paths.quant>/` (Stage 0 state, `edgellm_ckpt/`, `manifest.json`),
`config.yaml`, and `out/*.json`, `out/*.md`, `out/timing.jsonl`, `out/logs/`.

Hand-off for the device: `<paths.quant>/edgellm_ckpt/` goes to `tensorrt-edgellm` export. `difficulty.onnx` takes
`hidden [d_model]` (fp16) and returns the scalar `d`. The head only works with the backbone it was trained on:
`out/heads/version.json` records the Stage 0 manifest hash, the chat template hash, L_d and
`device_max_prompt_tokens`.

## Self-checks

| check | where |
|---|---|
| IRT recovers known a, b, theta (corr > 0.95) | `selfcheck.py irt` |
| identical `origin_query` across pools -> one item | `selfcheck.py dedup` |
| over-cap prompts excluded (not truncated), still in IRT | `selfcheck.py length_cap` |
| sharding covers each prompt once, token counts within 5% | `selfcheck.py sharding` |
| ONNX Runtime == PyTorch head, standardization folded | `selfcheck.py onnx` (random head); `export_onnx.py` (100 cached features) |
| fold check: folded fp16 vs ModelOpt fake-quant cos > 0.999; vs base clearly < 1 | `pilot.py` |
| early-stopped hooked forward == full `output_hidden_states` forward | `pilot.py` |

## Design notes and deviations found in the real data

- **Quantization recipe.** `quantize.py` calls Edge-LLM 0.11.0's own pieces: `build_quant_config("int4_awq")`,
  its 64-alignment int4 excludes (the GDN `in_proj_a/b` stay fp16), its calibration loader and loop (512 x 512
  tokens, batch 16), and its export (`export_hf_checkpoint` with AWQ resmoothing skipped for hybrid models). The
  one difference: Edge-LLM's loader builds the full VLM and, for Qwen3.5 AWQ, calibrates on images. We load the
  text-only `Qwen3_5ForCausalLM` (listed as supported by Edge-LLM) and calibrate on 512 train-split
  LLMRouterBench prompts. MTP draft weights are not exported.
- **Folding.** `mtq.fold_weight` writes quantize->dequantize weights once. AWQ's `pre_quant_scale` stays on each
  linear's (disabled) input quantizer and is applied exactly as ModelOpt does. No fake-quant op remains.
- **LLMRouterBench is not quite as described.** The release has 27 dataset dirs (24 after dedup merges the
  `arenahard_*` subsets and overlapping splits), 39 models, and an `openrouter` dir that is a router log, not a model
  (excluded via `labels.exclude_models`). GPT-5-medium is named `gpt-5`. 24,248 unique items; 3,367 are scored by
  both pools. `score` is 0/1, with 0.5 for arena-hard ties (count as correct at threshold 0.5) and `None` (missing).
  Pools are inferred from data: models run on `math500` (small) vs `hle` (flagship).
- **Leave-one-dataset-out** reuses the global IRT scale, so the held-out dataset's train items helped set `b_std`.
  The report states this caveat.
- **Layer indices** are 0-based decoder-layer indices; the saved state is that layer's output (residual stream).
  `L_d + 1` layers must run on device before routing.

## Stage A results (real data, CPU)

- 24,248 unique LLMRouterBench items (19,398 / 2,426 / 2,424 train/val/test); 3,367 shared between the pools.
- 374 items excluded by the 8,192-token cap (341 SWE-bench = 68% of SWE-bench, 28 ARC-AGI, 4 HLE, 1 Arena-Hard);
  excluded items keep their IRT `b_std`. 23,874 prompts remain.
- 9.2M prompt tokens. Extraction storage: 0.37 GB (last-token states at 3 layers).
- `train.py`, `evaluate.py` and `export_onnx.py` were dry-run on CPU against synthetic shards in `extract.py`'s
  exact format (real metadata, synthetic features).

## Results

Not run yet: these sections are filled from `out/eval_report.md` and `out/timing.jsonl` after the B200 session.

### Evaluation report

_pending_

### Timing report

_pending_

## Attribution

- **LLMRouterBench** (`NPULH/LLMRouterBench`, revision `0e5af1b84bf73437a01a1849c0f1d2468baa93fc`;
  github.com/ynulihao/LLMRouterBench): MIT. Each source benchmark keeps its own license (AIME, ARC-AGI, ARC-C,
  Arena-Hard, BBH, EmoryNLP, FinQA, GPQA, HLE, HumanEval, K&K, KOR-Bench, LiveCodeBench, LiveMathBench, MATH-500,
  MathBench, MBPP, MedQA, MELD, MMLU-Pro, SimpleQA, SWE-bench Verified, tau2-bench, WinoGrande).
- **Qwen3.5** (`Qwen/Qwen3.5-4B`, revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`): Apache 2.0, Qwen team,
  Alibaba Cloud.
- NVIDIA ModelOpt and TensorRT Edge-LLM (Apache 2.0).
