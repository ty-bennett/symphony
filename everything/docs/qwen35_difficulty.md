# Qwen3.5-2B + trained difficulty regression router

This is the preferred service/test profile. No training, LoRA, Gemma, Strands,
vision encoder, AWS SDK, or Snowflake client is required by this path. The older
classification backends remain available through explicit legacy profiles.

## Architecture and exact reuse

```text
actual messages → security scanner → Qwen chat template (thinking OFF)
                                         │
                       ONE full text-transformer prefill
                                         │
                       ┌─────────────────┴────────────────┐
                       │                                  │
               layer 10 residual                final normalized state
                last prompt token                  + hybrid cache
                       │                                  │
          trained difficulty.pt regression                │
                       │                                  │
          deterministic cutoff + security                 │
                /                 \                       │
             LOCAL             PRIVATE_AWS                 │
                │              handoff only                │
                └─────────────────────────────────────────┘
                           LM head → first token
                         decode only NEW token inputs
```

The feature is the **output residual stream of decoder layer 10 (zero-based)**,
before the final RMSNorm, at the last token of the assistant-generation prefix.
The hook clones only `[1, 2048]`; it does not retain all layer hidden states.
All 24 layers finish during that same prefill, so the complete cache is ready
even if the policy chooses LOCAL. There is no classifier instruction appended
to the prompt and no call to `generate(original_prompt)` afterward.

Qwen uses both attention K/V and GatedDeltaNet convolution/recurrent state.
We retain the entire Transformers `DynamicCache` object and reuse it in place.
The first generated token uses the final prefill state; generating N tokens
requires at most N−1 subsequent single-token transformer calls. A cloud route
discards the cache without invoking the local LM head. `/v1/decision` also never
invokes the LM head.

## Your pretrained head and score

`../router_heads/out/heads/difficulty.pt` and adjacent `version.json` are loaded
with `torch.load(weights_only=True)`. The actual architecture is standardization
→ Linear(2048, 256) → ReLU → Dropout(0.1) → Linear(256, 1). It runs in FP32,
eval mode, on the backbone device. It is not the old complexity/sensitivity MLP.

The output is **standardized 2PL IRT difficulty**. IRT estimates question
difficulty from benchmark model successes/failures; the training pipeline
standardizes using the training-item difficulty mean/std. Roughly, zero is
training-average difficulty, positive is harder, negative is easier. It is not
a probability of failure or a calibrated confidence. No sensitivity head is
present in this checkpoint.

Edit `config/difficulty_policy.yaml`:

```yaml
local_max_difficulty: 0.0
cloud_route: PRIVATE_AWS
```

`score <= cutoff` selects LOCAL; `score > cutoff` produces an AWS handoff.
**0.0 is an unvalidated starting cutoff**, not a trained deployment threshold.
Choose it using local-2B vs AWS quality/cost/latency evaluations. Increasing it
keeps more work local. Optional `difficulty` ground truth in test JSONL produces
RMSE/MAE; optional `route` labels produce routing accuracy.

## Provision the exact weights

Use the [weights link in router_heads/README.md](../../router_heads/README.md).
The currently linked [Drive export](https://drive.google.com/drive/folders/1G5vgaZqHA7QTsjRa5_DU7-n_Zj8rA2QI)
contains the checkpoint used to extract this head's training features.
Download **manifest.json plus the entire edgellm_ckpt folder** to:

```text
everything/models/qwen35-2b-router-awq/quant/
  manifest.json
  edgellm_ckpt/
    config.json
    hf_quant_config.json
    model.safetensors
    generation_config.json
    tokenizer.json
    tokenizer_config.json
    chat_template.jinja
```

`modelopt_state.pth` (~3.6 GB) is NOT needed by this inference path. The packed
export is about 1.7 GB. Keep the downloaded files unchanged; manifest and export
hashes are verified before loading. `QUANTIZED_MODEL_PATH` points to **quant/**,
not `edgellm_ckpt/`. An alternate location can be supplied using `--checkpoint`.

The head is pinned to Qwen/Qwen3.5-2B revision
`15852e8c16360a2fea060d615a32b45270f8a8fc`, manifest SHA256
`c3f1cc3fa0a36c9378e773374db75697fc879c0152a34bad1cd9959c62aa8522`,
the exact chat-template hash, hidden size 2048, layer 10, and a maximum prompt
capacity of 8192 tokens. Incompatible artifacts are rejected, not silently
substituted. The earlier **QuantTrio AWQ smoke-test weights are NOT compatible**.
Local hash checking proves artifact compatibility/integrity, not publisher
authenticity; provision these files through your approved local deployment path.

## Precision, memory, and limitations

The uploaded export is **ModelOpt 0.45 W4A16_AWQ**: symmetric signed INT4 grouped
weights (group size 128), FP16 activations, FP32 group scales, and FP16 AWQ input
scales. Embeddings, tied LM head, and excluded modules stay FP16. It is neither
W4A4 nor W8A8/SmoothQuant. The backbone/LM head are frozen.

The explicit reader follows [ModelOpt's packing layout](https://github.com/NVIDIA/Model-Optimizer/blob/0.45.0/modelopt/torch/export/quant_utils.py):
nibbles pack adjacent **output rows**, not input columns. No bitsandbytes or
ModelOpt installation is required for inference. Model construction uses meta
tensors and streams tensors from safetensors, without allocating another FP16
backbone. Tied embeddings and LM head share one tensor.

**This is a reference runtime, not a fused INT4 inference kernel.** It retains
packed weights and dequantizes a configurable output-row tile for FP16 PyTorch
matmul, then discards the tile. `AWQ_DEQUANT_CHUNK_ROWS=256` limits scratch
allocation, but increases kernel-launch overhead. Larger tiles trade memory for
speed. The Qwen GDN PyTorch fallback can be very slow without supported optimized
kernels. Do not interpret this runtime's throughput as NVIDIA engine benchmarks.

The adapter is tested against installed Transformers **5.18.x**; the head/export
were produced with 5.14.1. Actual checkpoint outputs and head accuracy across that
version/kernel change still need device validation. The unit tests cover the
actual head and synthetic exports, not a measured run of the entire 2B checkpoint.

An optimized TensorRT-Edge-LLM adapter must expose the layer-10 feature, final
state/first logits, and reusable complete cache in its prefill/decode API. The
earlier opaque `LLMRuntime.handle_request` smoke-test API does not provide those
hooks. We **do not** pair it with a second classifier model or perform a second
prefill. The head's ONNX metadata mentions JetPack 6.2/TensorRT 10.3; that does
not validate the separate JetPack 7.2/CUDA 13.2 smoke-test wheel on JetPack 6.x.

## Test and start

### Small Orin Nano functional + memory test

From the repository root, activate your CUDA Python environment and run:

```bash
python everything/scripts/test_qwen35_router_jetson.py --doctor \
  --checkpoint /absolute/path/to/quant

python everything/scripts/test_qwen35_router_jetson.py \
  --checkpoint /absolute/path/to/quant \
  --exercise-local --show-response --output orin-router-test.json
```

This is a **single small run, not an ablation**. It loads the exact backbone and
your trained head, prefills one built-in greeting prompt, predicts difficulty,
and tests greedy cache-aware generation for up to 16 tokens. Default context is
256 tokens to keep the first test small. No downloads, training, or remote calls
occur. `--exercise-local` explicitly tests LOCAL decoding even if the score
recommends AWS; the recorded routing recommendation is not changed. Without that
flag an AWS route prints `PASS_ROUTING_ONLY`, not a generation pass.

The terminal prints resident memory for the **backbone, LM head, difficulty head**,
unique total weights/buffers, prefill cache bytes, and overall peak PyTorch CUDA
allocation during inference. Resident sizes count actual parameter/buffer
storages, including packed weights and scales. The LM head shares embedding
weights, so it should report **0 additional resident bytes**. Do not add its
referenced weight size a second time. The old complexity/sensitivity heads are
not loaded in this configuration.

Per-component numbers are resident tensors, not independent activation peaks.
The overall peak includes runtime tensors, logits, cache and tiled dequantization
scratch tracked by PyTorch; CUDA driver/library allocations outside PyTorch are
not included. Reserved memory includes the allocator's cached blocks. This is
enough for a quick component-size/fit check, not a precise allocation attribution.
On Jetson, CPU/GPU share physical RAM; these numbers are not a separate VRAM bank
([NVIDIA memory architecture](https://docs.nvidia.com/cuda/cuda-for-tegra-appnote/index.html#memory-management)).

A full success prints `PASS` and `shared prefill verified: True`. The optional
JSON report contains measurements but no prompt, response, token IDs or hidden
vectors, and refuses to overwrite an existing file. `--show-response` only shows
the generated greeting in your terminal. The test checks execution/cache reuse,
not answer-quality accuracy. The existing reference-runtime performance caveats
still apply; no TensorRT native wheel is required for this test.

From the repository root, in a Python environment with compatible PyTorch and
the dependencies in `everything/pyproject.toml`:

```bash
# Read-only: does not allocate the backbone or download weights.
python everything/scripts/test_model.py --doctor

# Interactive real regression/router test with locally provisioned weights.
python everything/scripts/test_model.py \
  --checkpoint /absolute/path/to/quant --max-new-tokens 32

# Decision only: see score, route, security resolution, timing and prefill audit.
python everything/scripts/test_model.py \
  --checkpoint /absolute/path/to/quant --mode decision \
  --prompt 'Summarize the benefits of unit tests.'
```

The tester defaults to `everything/.env.qwen35-difficulty.example`. An explicit
`--device cpu` is possible for functional checks, but requires RAM and will not
represent Jetson performance. No downloads, retained prompts, automatic
training, or forced LOCAL fallback occur. `--demo` remains an explicitly labelled
legacy synthetic classifier fixture, not a regression quality test.

For the API, run from `everything/` with `src` on the import path:

```bash
cd everything
PYTHONPATH=src bash scripts/run_server.sh
# Or specify a copied literal env profile:
PYTHONPATH=src bash scripts/run_server.sh --profile /absolute/path/to/router.env
```

The launcher defaults to the new Qwen profile. Explicit environment values
override its defaults. One worker owns one backbone. Bind to localhost until
authentication/TLS and trusted security metadata handling are provided.

`GET /health`, `GET /v1/model/info`, `POST /v1/decision`, and `POST /v1/chat` are
unchanged. A decision now includes:

```json
{
  "difficulty": {"score": 0.43, "layer": 10, "target": "standardized_irt_difficulty"},
  "complexity": null,
  "sensitivity": null,
  "resolved_sensitivity": "SENSITIVE",
  "route": "PRIVATE_AWS"
}
```

Cloud chat returns `HANDOFF_REQUIRED` with the request ID and decision. AWS now
hosts the harder/frontier models; AWS model selection belongs to the remote
executor team. There is **no separate FRONTIER/Snowflake call** in this profile.
Local chat supports the existing streaming decision/token/complete SSE events.

## Security and head updates

Scanner flags and trusted SENSITIVE metadata remain authoritative. Because the
new head does not estimate sensitivity, UNKNOWN is resolved SENSITIVE. PUBLIC
requires trusted PUBLIC metadata and no scanner flags. Both LOCAL and approved
PRIVATE_AWS can process sensitive data; merely hosting a model in AWS does NOT
make its service approved—configure that infrastructure separately. This policy
never emits FRONTIER, even for PUBLIC or maliciously changed regression scores;
YAML attempts to use FRONTIER are rejected. No raw scanner matches, prompts,
responses, token sequences, or hidden vectors enter normal logs/reports.

Head snapshots can be atomically swapped/rolled back without reloading the LLM;
requests retain one snapshot. Architecture, layer, and feature identity cannot
change during a swap. This does not change the KV-cache path. No training is
needed to serve your artifact. The repository's older complexity/sensitivity
federation trainer is **not compatible with regression** and is explicitly
disabled/rejected in this profile; it must not silently train different heads.

## Tests and shared-prefill benchmark

```bash
cd everything
PYTHONPATH=src:. python -m pytest -q

# Source only your trusted local env profile for this env-driven benchmark.
set -a
source .env.qwen35-difficulty.example
set +a
PYTHONPATH=src:. python scripts/benchmark_shared_prefill.py --max-new-tokens 32
```

The benchmark compares naive two-prefill vs shared one-prefill paths using the
same model instance, including attention + GDN cache bytes, prompt-token counts,
TTFT, throughput, total latency and peak CUDA allocation. Unit tests independently
hook every actual tiny-Qwen decoder layer, compare cached greedy decoding with
full recomputation, load your trained head, validate packed AWQ arithmetic/export
integrity, reject incompatible swaps, and assert cloud/decision-only do not call
the LM head. Full-model integration is opt-in via `QWEN_DIFFICULTY_QUANT_DIR`.

Real Orin Nano fit, latency, optimized GDN support, and the optimal routing cutoff
remain to be measured on the device. Exact feature/logit parity with the training
runtime also remains to be evaluated before production rollout.
