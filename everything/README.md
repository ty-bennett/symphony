# Shared-prefill edge router

Python LOCAL/on-premise backend for privacy-aware LLM routing. One frozen causal
transformer supplies both routing features and the cache for local generation.
PRIVATE_AWS and FRONTIER return structured handoffs; neither has a network client.
On-device federation trains and replaces **only the small decision heads**.

## Qwen3.5-4B AWQ Orin Nano smoke test

A separate, opt-in [Jetson smoke test](docs/qwen35_awq_jetson.md) checks whether a
real **INT4 AWQ / FP16-activation** Qwen3.5-4B text model loads and generates on
your device. It does not change the Gemma/Strands router or require training.
Start with read-only diagnostics on the Jetson:

```bash
python3 everything/scripts/test_qwen35_awq_jetson.py doctor
```

The pinned NVIDIA native runtime path requires JetPack 7.2/CUDA 13.2 and Python
3.10–3.12; **JetPack 6.x is reported as incompatible with this wheel**, not as a
model-fit failure. The linked guide includes isolated installation, explicit
weight download/build/run commands, smaller-context retries, memory/throughput
reports, and the experimental AWQ builder caveat. No CPU/FP16 fallback, remote
inference, decision-head training, or vision engine is used.

## Test the model interactively or with a dataset

From the repository root, the tester works with the existing virtual environment;
its wrapper locates source/config files without requiring an editable reinstall:

```bash
# Read-only diagnostics; no model allocation or downloads.
.venv/bin/python everything/scripts/test_model.py --doctor

# Try the interface immediately, entirely offline. NOT a real Gemma quality test.
.venv/bin/python everything/scripts/test_model.py --demo

# Actual INT8 Gemma/Strands, after provisioning your approved exported artifact.
.venv/bin/python everything/scripts/test_model.py \
  --checkpoint /absolute/path/to/gemma4-strands-sq-int8
```

The real-model tester defaults to the repository's Gemma 4/Strands profile.
`--profile PATH` selects another **literal** env profile (never shell-executed).
Profile values override environment settings; explicit CLI flags override those.
Model/config paths inside the profile are resolved relative to `everything/`;
`--checkpoint` and input/output paths are relative to your calling directory.
Loading requires an approved matching checkpoint and suitable hardware. CPU-only
PyTorch or a missing artifact is reported as a blocker, **not** a silent demo
fallback. `--device cpu` explicitly tests on CPU if sufficient RAM is available;
its performance numbers are not Jetson measurements.

Type a prompt and see complexity/sensitivity probabilities, the resolved route,
policy reason, streamed **LOCAL-only** response, prompt/generated-token counts,
prefill/decision/end-to-end latency, time to first token, decode tokens/sec, peak
CUDA allocation when available, and a per-request single-prefill audit. `/info`
shows the loaded model/version/precision/quantization details; `/quit` exits.
Each prompt is an independent conversation. Remote routes remain handoffs:
the tester never forces generation or sends anything to AWS/Snowflake.

```bash
# One prompt (don't put secrets in shell history/process arguments).
.venv/bin/python everything/scripts/test_model.py \
  --checkpoint /absolute/path/to/int8-artifact \
  --prompt 'Reply with the word hello.' --max-new-tokens 32

# Decision-only batch, with aggregate quality/performance metrics.
.venv/bin/python everything/scripts/test_model.py \
  --checkpoint /absolute/path/to/int8-artifact \
  --cases everything/examples/test_cases.jsonl --mode decision \
  --output routing-test-report.json
```

JSONL cases contain `messages`, optional `security_context`, and optional expected
`complexity`, `sensitivity`, and `route` labels. These expected labels only score
predictions: they are **not** injected as trusted metadata. Reports include
accuracy, per-class precision/recall, confusion matrices, sensitive false-negative
and false-positive rates, route accuracy, and two distinct security counts:
ground-truth sensitive examples reaching FRONTIER (including undetected learned
errors), and deterministic **resolved SENSITIVE → FRONTIER** violations. Exit 1
flags either security count; exit 2 means a configuration/load/request failure.
The six supplied examples are synthetic, manually labelled smoke cases, not a
representative enterprise benchmark. Precision/recall require labelled data; a
single unlabeled prompt does not estimate accuracy, and model confidence is not
measured precision. Demo results are explicitly marked invalid as real quality
evidence. Inspect real generation quality yourself or use your approved evaluation
dataset; the tester does not fabricate an automatic response-quality score.

Use `--mode chat` for routed generation, `--classification SENSITIVE` to exercise
trusted security overrides, `--system` for your production system message, and
`--temperature`/`--top-p`/`--max-new-tokens` for sampling. Batch `--warmup N` makes
N explicitly unscored requests before measurement; default is zero. Startup/model
load time is shown separately, and TTFT is measured from request start to the first
generated token (including prefill and routing). Decision-only and remote requests
have no TTFT/generation metric.

No training, feedback collection, federation, or prompt/response retention is
enabled. Output text is displayed **only in your terminal**; be careful with screen
sharing/terminal capture. Opt-in `--output` stores metadata/aggregate metrics, never
prompts, responses, hidden vectors, token sequences, or raw secret matches; reports
refuse overwriting existing files. Application metadata logs are quiet by default;
`--metadata-logs` displays structured content-free logs. Downloads are denied by
default, regardless of the profile. To deliberately test original (not INT8)
weights before calibration, on a sufficiently large machine with approved Hub
access, use `--unquantized --allow-download` and explicitly select its device.
This is a separate reference test, **not** an INT8 performance result.

### Provision the real weights on this laptop

The download helper fetches exact matching revisions into the git-ignored local
`everything/artifacts/hub` cache; it sends no prompts, responses or training data.
The original Gemma package is about 16 GB and Strands adds about 435 MB. The
original package includes multimodal tensors, but the backend only instantiates
the text model. Downloads are resumable; no remote model code is executed.

```bash
# From the repository root. Downloads are explicitly requested by this command.
.venv/bin/python everything/scripts/download_real_model.py

# Build a REAL INT8 smoke artifact with six synthetic forward-only calibration cases.
# This is not decision-head training and is not production-quality calibration.
.venv/bin/python everything/scripts/quantize_smoothquant.py \
  --profile .env.gemma4-strands.cpu.example \
  --hub-cache everything/artifacts/hub \
  --calibration-jsonl everything/examples/test_cases.jsonl \
  --output everything/artifacts/gemma4-strands-sq-int8-smoke --max-examples 6

# Interactive real INT8 test, offline, on CPU.
.venv/bin/python everything/scripts/test_model.py \
  --profile .env.gemma4-strands.cpu.example
```

This profile intentionally selects CPU. Check NVIDIA driver availability and use
a compatible CUDA PyTorch build before GPU testing; the E4B model's large
embedding tables mean INT8 decoder linears alone may not fit an 8 GB GPU.
CPU calibration can take several minutes and requires the original model's RAM
footprint plus conversion working memory. A synthetic smoke artifact proves the
real path runs; validate with approved representative calibration/evaluation data
before using routing scores in production. Exported artifacts are immutable, so
use a fresh output path if repeating conversion.

The real CPU smoke run on this laptop (2026-10-10) returned `hello` with route
`LOCAL`: 370 prompt tokens processed in exactly one prefill, followed by one
cached decode input (two generated tokens including EOS). The request took about
99.5 seconds, excluding about 15.7 seconds of model startup. The exported smoke
artifact is 11,108,386,280 bytes (about 10.35 GiB). These are functional CPU
measurements with six synthetic calibration cases, not Jetson benchmarks or
decision-quality validation.

## Architecture

```text
    User Request
         │
         ▼
    Security Scanner
         │
         ▼
    Tokenizer / Chat Template
         │
         ▼
    ┌──────────────────────────────┐
    │ Shared Transformer Backbone │
    │                              │
    │    ONE PROMPT PREFILL        │
    └──────────────┬───────────────┘
                   │
          ┌────────┴────────┐
          │                 │
    Hidden State         KV Cache
          │                 │
          ▼                 │
    Decision Heads          │
          │                 │
          ▼                 │
    Deterministic Policy    │
       /      |      \      │
      /       |       \     │
    LOCAL   PRIVATE   FRONTIER
      │      AWS
      │
      └───────────────┐
                      ▼
                   LM Head
                      │
                      ▼
              decode using existing
                   KV cache
```

`SharedBackboneBackend` defines `prefill`, `classify`, and `decode_from_prefill`.
Two inference configurations are available: Gemma 3 with separately trained MLP
heads, and **Gemma 4 + pretrained Strands** with appended structured requirements
(below). Both process their complete production prompt exactly once.

The Gemma 3 adapter calls `Gemma3ForCausalLM.model` directly with `use_cache=True`
and `output_hidden_states=False`. It clones only the final token's normalized
hidden representation into `PrefillContext`; no intermediate layer states are
retained. Both FP32 MLP heads consume that exact representation at the assistant
generation position. There is no classifier instruction or separate transformer.

For LOCAL, the LM head computes first-token logits from that saved vector. Each
subsequent transformer call receives **one newly generated token**, the existing
cache, the extended attention mask, and the absolute cache position. The prompt
is never passed to `generate()` after routing. A context is consumable once.
Remote decisions discard their KV cache and never invoke the LM head.

The backbone and LM head have `requires_grad=False`. The Gemma 3 path uses no
LoRA. The Gemma 4 path uses the **same permanently enabled, frozen pretrained
adapter** for routing and generation; it never switches adapters after prefill.
Changing weights between the two paths would invalidate cache reuse. The Gemma 3
heads are two small
heads use LayerNorm → Linear(256) → SiLU → Dropout → Linear(classes).
Temperature-scaled probabilities are returned; temperatures default to 1 until
fitted on held-out data. Untrained Gemma/GPT-2 heads explicitly fail closed to
PRIVATE_AWS until an administrator provides a decision checkpoint.

## Pretrained Gemma 4 + Strands: appended requirements

This experimental configuration needs **no initial custom decision-head training**.
It loads the matching Gemma 4 text decoder, its original LM head, and the full
pretrained Strands LoRA and pointer-head artifacts. Only one backbone is allocated;
no vision/audio encoder or separate decision transformer is instantiated.

```text
Native chat conversation + original final user request
    + complexity choice question / options / <answer>
    + sensitivity choice question / options / <answer>
    + instruction to answer the original request
    + native assistant-generation suffix (thinking disabled)
                         │
                  ONE complete prefill
                         │
             final-layer output + native KV cache
                /                 |                  \\
    complexity marker/       sensitivity marker/    final assistant state
       option vectors           option vectors            │
             │                       │                    LM head
    pretrained pointer       pretrained pointer            │
             └──────── policy ────────┘           LOCAL → cached decoding
```

The rubric lives in `config/strands_requirements.yaml`. Requirements are appended
inside the final user turn, before the chat template's assistant marker; original
messages are not duplicated. Requests must end with a user message. A **single
tokenization of the complete chat prompt** supplies character offsets for our own
inserted markers/options, so user-supplied lookalike text cannot change readout
positions. No new embedding tokens are added. The backend collects seven selected
final-layer vectors and the final assistant vector, without retaining all layers.
Decision labels are read directly by the small heads, not generated by the LLM.

LOCAL uses the final assistant vector for first-token logits and the complete
combined-prompt cache for every later token. Subsequent forwards contain one new
token only. Neither decision question gets its own transformer pass. A remote
decision never invokes the LM head. There is no cache-fork or repeated-prefill
fallback. This intentionally replaces the original **one identical final vector**
requirement with **different token positions from the same transformer forward**.

Install from the repository root on a machine with enough RAM/VRAM:

```bash
source .venv/bin/activate
python -m pip install -e './everything[dev,strands]'
# Review this profile first. Gemma access must already be authorized locally.
set -a
source .env.gemma4-strands.example
set +a
cd everything
# First produce the approved INT8 artifact, as described below.
bash scripts/run_server.sh
```

`STRANDS_CHECKPOINT` accepts a provisioned local directory or Hub ID.
`STRANDS_REVISION` should be a reviewed Hub commit in deployment. The loader
requires safetensors, validates head/adapter digests against provenance, requires
a matching pinned Gemma commit, and rejects incomplete head/LoRA weights or base
weights. It downloads no remote code. Digests establish consistency, **not publisher
authenticity**: bootstrap artifacts must be administrator-approved/provisioned;
incoming federated packages still use the independent signature verifier/gate.
`MODEL_REVISION=main` resolves to the recorded training commit, not current Gemma
main; an explicitly incompatible revision is rejected. Only dense Gemma 4 E2B/E4B
checkpoints are supported; use the matching Strands package when selecting E2B.

Two independent FP32 pointer heads start from the same pretrained readout, so each
can be federated independently. The entire LoRA stays frozen, including during
local training. Frozen feature extraction uses the exact same combined prompt and
seven vectors. Adapter identity, tokenizer, chat format, and rubric fingerprints
bind decision checkpoints to this configuration. Hot swaps/rollback change heads
only, not the backbone or cache behavior. Existing training/evaluation, federation,
and benchmark commands work with the exported Gemma 4 profile; none is required
to bootstrap it. Background training still requires explicit approved local labels
and retention; inference does not silently collect examples.

### INT8 SmoothQuant (no training)

The Gemma 4 profile now selects `QUANTIZATION=smoothquant_int8` with
`DTYPE=float16`. This is actual INT8 storage and integer matrix multiplication,
not FP16 casting or floating-point fake quantization. Based on the
[SmoothQuant method](https://arxiv.org/abs/2211.10438), it uses offline calibrated
channel smoothing, per-output-channel signed INT8 weights, and dynamic
per-token INT8 inputs for the decoder's attention/MLP linear projections.
The frozen Strands LoRA is **merged before calibration/quantization**, so the
single effective adapted backbone is shared by routing and local generation.
There is no adapter toggle, second backbone, extra request-time calibration,
or second prompt prefill. A conversion process is separate from serving.

Embeddings (including PLE), PLE projections, LM head, residual stream, attention
BMMs, and KV cache remain FP16. Decision heads/probabilities remain FP32.
INT32 GEMM accumulation and FP32 scaling return outputs to FP16. This is
**W8A8 linear-layer quantization**, not an entirely INT8 transformer or KV cache.
To preserve Gemma's norm/residual behavior, the inverse smoothing scale is
applied at each linear input rather than folded into a preceding norm; that
unfused operation adds overhead. No inference speedup is claimed yet.

Calibration only runs forward passes; it requires no labels, optimizer,
backpropagation, or custom decision-head training. Supply an explicitly approved
**local** JSONL file with representative production-style conversations:

```json
{"messages":[{"role":"user","content":"Summarize this public release."}]}
```

With the profile exported, run these commands from `everything/`:

```bash
python scripts/quantize_smoothquant.py \
  --calibration-jsonl /approved/local/calibration.jsonl \
  --output artifacts/gemma4-strands-sq-int8 --alpha 0.5 --max-examples 128
bash scripts/run_server.sh
python scripts/benchmark_shared_prefill.py --max-new-tokens 32
```

Calibration needs the original model's memory footprint, so prepare the artifact
on a sufficiently large machine, then provision the immutable directory locally.
Prompts/labels/responses/features are not exported or logged; only model tensors,
tokenizer, rubric, provenance hashes, and aggregate conversion metadata are saved.
Calibrated model artifacts may still reflect calibration data; they are not claimed
to be anonymized. No arbitrary user-content collection or remote calibration
service exists. Input errors do not echo raw calibration records.

Serving loads the compressed safetensors artifact into a meta-initialized model,
one tensor at a time, without first loading the original FP16 weights or LoRA.
It validates digests, the base revision, tokenizer/chat/rubric identity, tensor
shapes/dtypes, finite parameters, and positive scales. Digests are integrity
checks, **not signatures**: bootstrap backbone artifacts must be administrator
approved, immutable, and protected from concurrent modification. The quantized
backbone fingerprint also binds federated heads/updates, preventing mixing
clients or checkpoints with incompatible quantized representations.

The integer kernel is native PyTorch `torch._int_mm` (INT8 × INT8 → INT32),
isolated behind the quantization module for future TensorRT/CUTLASS replacement.
It pads dimensions/one-token decode rows for kernel alignment and probes the
installed CPU/CUDA build before use. This private ATen interface is a portability
risk: validate the actual JetPack PyTorch build, GPU kernel, memory, generation,
and head accuracy on Jetson before deployment. A missing artifact, bad calibration,
or unavailable kernel fails explicitly; there is **no silent FP16 fallback**.
`/v1/model/info` reports the quantization scope, recipe, kernel, and merged-adapter
state. Existing cache/security tests also run against a tiny real INT8 Gemma 4.

For an explicit small-model native CUDA/cache smoke test on the deployment machine:

```bash
RUN_SMOOTHQUANT_CUDA_TESTS=1 pytest -q tests/test_gemma4_strands.py -k smoothquant_cuda
```

The converter reports linear storage, total weight/quantization storage (excluding
runtime/KV/decision-head overhead), artifact bytes, conversion time, and peak CUDA
allocation if available. These are measured sizes, not an assumed 2× whole-model
memory reduction.

**Quality limitation:** the pretrained Strands questions were evaluated separately,
not packed into this chat layout. The second question now sees the first, and local
generation sees both. Routing accuracy, sensitivity recall, generation quality,
prompt-injection robustness, and probability calibration on this combined format
need validation before production. Published choice temperatures are preserved as
starting values, not claimed to be calibrated for enterprise routing. `/v1/model/info`
reports that limitation. The deterministic scanner/metadata/policy remains
authoritative; high learned PUBLIC confidence is never a complete DLP guarantee.

**Hardware limitation:** unquantized E4B text weights are approximately 15 GB in
FP16/BF16 and **do not fit an 8 GB Orin Nano**. SmoothQuant reduces the selected
linear-weight storage, not the large embeddings/PLE tables or KV cache; INT8 does
**not guarantee** E4B fits in 8 GB. Inspect actual checkpoint/CUDA memory and allow
OS/runtime/cache headroom. Q4/INT4 and embedding compression remain future work.
This is an HF prototype, not measured Jetson deployment readiness.

```bash
pytest -q
# Opt-in only: downloads/provisions real model weights, requires sufficient memory.
RUN_STRANDS_MODEL_TESTS=1 pytest -q tests/test_strands_integration.py
python scripts/benchmark_shared_prefill.py --max-new-tokens 32
```

Offline tests instantiate tiny **real Gemma 4 transformers with nonzero PEFT LoRA**:
they check one prefill, all later inputs length one, full-prefix/cached-logit parity,
sliding-window eviction, K/V-sharing layers, PLE, text-only checkpoint mapping,
pretrained-shaped head loading, privacy-safe handoffs, head-only training, independent
federation, adapter/rubric compatibility, hot swapping, and unchanged backbone/LM
parameter hashes. These do not establish real E4B routing or response quality.

Implementation references: [Strands pointer head and checkpoint layout](https://github.com/strands-labs/strands-decider/blob/main/src/strands_decider/modeling.py),
[Strands typed question renderer](https://github.com/strands-labs/strands-decider/blob/main/src/strands_decider/prompting.py),
[Gemma 4 model documentation](https://huggingface.co/docs/transformers/main/en/model_doc/gemma4).

## Security policy

**FRONTIER is an allow-list destination, not the default fallback.**

The YAML matrix in `config/routing_policy.yaml` is independent of all learned
weights and calibration. Its defaults are:

| Resolved sensitivity | LOW | MEDIUM | HIGH |
|---|---|---|---|
| PUBLIC | LOCAL | PRIVATE_AWS | FRONTIER |
| SENSITIVE | LOCAL | PRIVATE_AWS | PRIVATE_AWS |

The learned SENSITIVE label, trusted SENSITIVE metadata, scanner flags, and
uncertain/invalid sensitivity probabilities can only escalate sensitivity.
Trusted PUBLIC metadata cannot downgrade any of those signals. Uncertainty
defaults to PRIVATE_AWS. FRONTIER additionally requires PUBLIC probability ≥0.95,
no security flags, no trusted SENSITIVE metadata, and sufficient sensitivity
confidence. Invalid YAML that maps SENSITIVE or uncertainty to FRONTIER is
rejected, and a final explicit guard enforces the invariant independently.

The scanner recognizes private keys, AWS access-key patterns, labelled passwords
and config secrets, bearer/API tokens, SSN-like patterns, and credential URLs.
Only flag codes leave it; matches and secret text are never returned or logged.
Patterns are intentionally imperfect: this is an escalation layer, not complete
DLP. The absolute deterministic invariant applies to **resolved SENSITIVE** and
trusted/detected sensitivity. No classifier plus imperfect pattern scanner can
guarantee detecting every semantically sensitive sentence. An enterprise needing
that stronger guarantee must supply authoritative classification/DLP or disable
PUBLIC → FRONTIER in YAML. Evaluation reports undetected sensitive false negatives
honestly rather than injecting ground truth into runtime metadata to hide them.

The harness must authenticate and authorize producers of `security_context`;
untrusted end users must not manufacture trusted security classifications.
This milestone binds to loopback and leaves authentication/TLS to that harness.

## Install and run

Use Python 3.11+ from the repository root. For CPU development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[dev]'
MODEL_BACKEND=tiny bash scripts/run_server.sh
```

The offline `tiny` backend is a deterministic test fixture, not an LLM. It needs
no Hugging Face access and returns deliberately conservative decisions. For an
actual small development model:

```bash
MODEL_BACKEND=gpt2 MODEL_ID=sshleifer/tiny-gpt2 DEVICE=cpu \
  bash scripts/run_server.sh
```

GPT-2 has an explicit adapter and a simple role template when its tokenizer has
no native chat template. Train decision heads before expecting useful routing.
Gemma access requires accepting its model license and provisioning Hugging Face
credentials outside source control. Model loading is the only built-in network
access; `LOCAL_FILES_ONLY=true` uses an already provisioned local cache.

CUDA deployment (install the PyTorch wheel appropriate for CUDA/JetPack first):

```bash
MODEL_BACKEND=gemma3 MODEL_ID=google/gemma-3-4b-it \
  MODEL_REVISION=<pinned-hugging-face-commit> DEVICE=cuda DTYPE=bfloat16 \
  DECISION_CHECKPOINT=artifacts/approved-heads bash scripts/run_server.sh
```

Use **one Uvicorn worker**: multiple workers each load another backbone. Within
the process an execution lock serializes backbone/cache work at batch size 1.
Training waits for that lock only during feature extraction; head optimization
does not mutate or lock live inference heads. SSE uses one producer thread,
a bounded queue, and cancellation so model locks remain owned by one thread.

Environment variables are shown in `.env.example`; the app reads environment
variables directly, so export them yourself (it does not implicitly load `.env`).
YAML controls the policy and federation/training. `QUANTIZATION=none` selects the
unquantized adapters. Gemma 4 + Strands also supports `smoothquant_int8`, requiring
an offline calibrated artifact at `QUANTIZED_MODEL_PATH`. Unsupported methods
raise an explicit error; they do not silently fall back to floating-point weights.

## Frontend API

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/model/info
curl -X POST http://127.0.0.1:8000/v1/decision \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Explain caching."}],"security_context":{"classification":"UNKNOWN"}}'
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hello."}],"generation":{"max_new_tokens":32,"temperature":0,"top_p":1}}'
```

`/v1/decision` runs scanner + prefill + heads + policy only. It returns predictions
with full probability distributions, resolved sensitivity, flag codes, policy
reason, prompt token count, head version, request ID, and timings.

`/v1/chat` returns `COMPLETED` with text for LOCAL, or `HANDOFF_REQUIRED` with
route, request ID, and decision for PRIVATE_AWS/FRONTIER. `HandoffExecutor` performs
no I/O. Future AWS/Snowflake implementations conform to `RemoteExecutor`, which
receives the request and enforced decision; the router needs no modification.

Add `"stream":true` to a chat request and use `curl -N` for SSE events `decision`,
`token`, `complete`. Handoffs emit `decision` and `complete`. Execution errors emit
only a generic error code. Hidden vectors, cache contents, and token IDs are
never exposed. Greedy decoding uses temperature 0; temperature >0 enables
sampling with optional nucleus filtering. EOS IDs are taken from the model's
generation configuration and tokenizer. Prompt/generation limits are rejected
explicitly; prompts are never silently truncated.

## Gemma assumptions

The original Transformers 4.57.6 implementation and the now-installed
**Transformers 5.18.0** implementation were inspected before adapting their
internals. Dependencies now pin the validated 5.18.x minor version, including
Gemma 4 support. The Gemma 3/GPT-2 regression tests pass on 5.18.0. Reference:
[Gemma 3 Transformers documentation](https://huggingface.co/docs/transformers/v4.57.0/en/model_doc/gemma3).

Only `Gemma3ForCausalLM` with the text config is instantiated, never the vision
encoder. An explicit text-only subclass disables Gemma's legacy base-prefix
stripping and maps both older `language_model.model.*` and newer
`model.language_model.*` checkpoint keys to `model.*`. Missing text weights and
unexpected text layouts are fatal. Multimodal shards may still need downloading
and reading during conversion, but no vision module is allocated; provision a
text-only export if storage/download overhead matters.

Gemma's native template excludes the system role. The adapter deterministically
folds a leading system message into the first user message, then invokes the
normal template with `add_generation_prompt=True`. The same formatting function
and template fingerprint are used in inference, training, and validation.

The text transformer creates a config-aware `DynamicCache` containing full and
sliding cache layers. Transformers owns mask generation and sliding eviction.
The adapter supplies absolute positions (`position_ids` on 5.18;
`cache_position` on the older 4.57 implementation), never inferring positions from
the length of an evicted sliding-layer tensor. LM-head projection covers one
token only and reproduces Gemma's optional final-logit softcapping. Full-model
reference tests use `logits_to_keep=1` and compare reused-cache greedy tokens.

## Local head training and evaluation

JSONL records contain production `messages`, `complexity`, `sensitivity`, and
optional `label_source`, timestamp, outcome metadata, route label, and security
context. Label sources: HUMAN_CONFIRMED, TRUSTED_SECURITY_METADATA, ADMINISTRATOR,
ROUTE_OUTCOME, AUTOMATED_HEURISTIC. Explicitly supplied administrator datasets
default to ADMINISTRATOR. Sensitivity labels from ROUTE_OUTCOME or heuristics are
excluded from sensitivity loss; successful remote execution does not imply PUBLIC.
Synthetic, non-production examples are in `examples/`.

```bash
MODEL_BACKEND=tiny python -m training.train_decision_heads \
  --data examples/train.jsonl --output artifacts/demo-heads \
  --validation-data examples/validation.jsonl --calibrate
MODEL_BACKEND=tiny DECISION_CHECKPOINT=artifacts/demo-heads \
  python -m training.evaluate_decision_heads --data examples/validation.jsonl
```

Choose a new immutable output directory for each checkpoint. Replace `tiny` with
your target model for meaningful features; synthetic fixtures are not a training
corpus or evidence of production quality. `--calibrate` fits two temperatures on
held-out data without changing policy thresholds. Class weights and loss weights
are configured in `config/federation.yaml`; both heads can be enabled separately.

Every transformer/LM parameter is frozen. Feature extraction uses inference mode,
detaches the final assistant-position vector, then clones outside inference mode
so autograd can save head inputs. ONLINE_FEATURE_EXTRACTION repeats extraction
each epoch; PRECOMPUTED_FEATURES extracts once and optimizes heads repeatedly.
The trainer also accepts compatible precomputed features directly and reports
local aggregate accuracy/recall. A version fingerprint prevents using features
from another backbone/template. Features stay local and are potentially sensitive,
**not anonymized**. `FeatureStore` is a deployment storage abstraction; the built-in
store is bounded, expiring, in-memory only.

Evaluation reports both confusion matrices, per-class precision/recall and
accuracy, SENSITIVE false-negative and PUBLIC false-positive rates, optional
route accuracy, sensitive-labelled examples reaching FRONTIER, and separate hard
policy-invariant violations. Evaluation exits unsuccessfully if any sensitive
example reaches FRONTIER. Never hide classifier mistakes by converting every
test sensitivity label into trusted runtime metadata.

## On-device federation

Federation is disabled by default and has no request-time network dependency.
`FederationAdapter` supports global-model receipt, delta submission, and aggregate
metrics. Implement an NVIDIA FLARE or Flower adapter separately; neither is a core
dependency. This repository does not implement a production aggregator.

```text
Approved global head package → signature + compatibility + local gate
                                         ↓
Local examples → frozen feature extraction → isolated FP32 head optimization
                                         ↓
                           head-only parameter deltas
                                         ↓
                         finite validation + L2 clipping
                                         ↓
                       FederationAdapter / secure aggregation
```

`FederatedLocalTrainer` copies approved heads and never trains the manager's live
weights. Its optimizer contains only enabled head parameters. FedAvg-compatible
deltas include per-head eligible-example counts for non-IID clients, backbone /
tokenizer / template identities, round/version, aggregate scalars and norm.
A configurable proximal penalty supplies the FedProx hook. Private training RNGs
control shuffle/dropout without reseeding concurrent inference sampling. The
closed outbound schema has no prompts, responses, token IDs, feature vectors, matches, per-example
losses, or gradients. Unknown metrics and non-finite/malformed deltas are rejected.
Norm clipping occurs before submission. It is a robustness measure, not a DP claim;
model updates themselves can leak information.

`FederatedClient.run_round` belongs in a background worker, taking an explicitly
approved local dataset. It verifies the global package, validates/activates newer
global heads, trains an isolated copy, and submits clipped updates and aggregates.
Server outages, invalid packages, bad candidates, insufficient labels, or rejected
updates return failure while serving the last known-good heads.

Global checkpoints contain only `heads.safetensors`, `config.json`,
`calibration.json`, `manifest.json`, and optionally `signature.bin`. Metadata binds
schema, round/version, base ID/revision, tokenizer/template hashes, hidden size,
label order, architecture/width/dropout, and creation time. Incoming weights must
match exact keys, shapes, FP32 dtype, and finite values. Buffers are verified and
loaded once to avoid file-change races. No pickle or remote model code is loaded.

`Ed25519ModelPackageVerifier` uses the standard `cryptography` library
(`pip install -e '.[signatures]'`). Provision its public key out of band. The
federation authority signs the exact `manifest.json` bytes; that manifest contains
SHA-256 hashes of all three artifacts. Missing signatures and altered metadata,
calibration, or head files are rejected. The development verifier permits unsigned
packages and is forbidden by the production lifecycle. `DECISION_CHECKPOINT` is
an administrator-provisioned startup artifact; incoming federation packages must
always use the verifier/lifecycle, never that startup helper.

`FederatedModelGate` requires protected local PUBLIC and SENSITIVE examples,
finite output shapes, compatible hidden size, complexity accuracy, sensitivity
recall/FNR, and zero routing/security violations. Default thresholds are 0.70
complexity accuracy, 0.98 sensitivity recall, and 0.02 maximum sensitive FNR.
Candidates failing the gate cannot activate. Validate those thresholds on the
deployment's protected corpus, especially under non-IID data or future DP noise.

`DecisionHeadManager` publishes an immutable-by-convention snapshot atomically.
Requests capture one version before prefill and complete on that version. Swaps
and rollback retain previous heads without touching the backbone or any KV cache.
Checkpoint stages are immutable, an `active.json` pointer is replaced atomically,
and `restore_active()` reverifies and reevaluates it after restart. A round high
watermark rejects replays even after rollback. The manager is thread-safe within
the single serving process; a multi-process deployment needs supervisor
coordination and would otherwise duplicate the LLM.

Production refuses federation when required secure aggregation is unavailable.
The simulation adapter honestly reports no secure aggregation, so its demo opts
out in development only. Secure aggregation must be supplied by the external
framework; no custom cryptography is implemented. DP configuration exists but
enabling it raises an explicit unsupported error until an Opacus integration and
accountant are supplied. No privacy guarantees are advertised without a mechanism.

```bash
python scripts/simulate_federated_round.py
```

Three synthetic non-IID clients train compatible deltas, a small development
coordinator averages them, and a gated round-1 package activates. Simulation uses
one tiny backbone per simulated deployment, never a duplicate in one edge service.

Feedback is explicit: `FeedbackService` connects corrected labels to original
messages only in an approved `LocalDataStore`. `InMemoryLocalStore` defaults to
retention disabled and has TTL/capacity bounds when enabled. The API does not
silently retain user messages. A harness can explicitly retain approved requests
and submit feedback; there is no federation API for retrieving that content.
Supply approved encrypted storage through these interfaces rather than custom
encryption. No examples are written to normal application logs.

## Verification and benchmarks

```bash
pytest -q
ruff check src training scripts tests
ruff format --check src training scripts tests
MODEL_BACKEND=tiny python scripts/benchmark_shared_prefill.py --max-new-tokens 16
MODEL_BACKEND=tiny python scripts/benchmark_local_head_training.py --data examples/train.jsonl
```

Tests include the real Gemma transformer initialized with tiny random weights,
not just mocks. Hooks record one prompt-length call followed only by single-token
calls. Cached tokens match full-prefix logits across sliding-window boundaries.
Synthetic old/new multimodal checkpoints prove text-only weight mapping without
a vision encoder. Other tests cover all policy/security overrides, sanitized logs
and errors, API/SSE/handoffs, head-only parameter hashes including the LM head,
update contents/clipping/provenance, signatures, compatibility, candidate quality,
atomic hot swaps/in-flight consistency, rollback, and federation outage isolation.

Downloaded-model tests require explicit opt-in:

```bash
RUN_REAL_MODEL_TESTS=1 MODEL_BACKEND=gpt2 MODEL_ID=sshleifer/tiny-gpt2 \
  pytest -q -m integration
```

The prefill benchmark deliberately compares two prefills against one using the
**same loaded backbone**. After warmup it alternates measurement order and reports
prompt tokens, prefill and head timings, TTFT, throughput, total time, peak CUDA
memory, modern-cache storage bytes, backbone instances, and observed token/call
counts. It asserts two versus one prefill and the corresponding prompt token
counts. GPU measurements synchronize; tiny fixtures demonstrate accounting, not
Gemma performance or a production speedup.

The training benchmark compares online extraction each epoch with extraction once
plus repeated head optimization, and separately runs optimization from already
precomputed features with zero backbone calls. It reports extraction/training time, CUDA peak,
CPU peak RSS (Linux KiB), checkpoint/update sizes, and full before/after backbone
and LM parameter hashes. Hashing a 4B model is deliberately expensive and outside
request time; host transfers are chunked to bound audit memory. Outputs use temporary local
directories and contain only synthetic/approved local artifacts.

Structured application logs contain only request IDs, route/labels/confidences,
token counts, and latencies. The metrics interface ships bounded in-memory
aggregates; production can implement a Prometheus exporter. Federation metrics
include status, round/version, attempts/successes, example count, duration, clipping
and norm. `UNSAFE_CONTENT_LOGGING=true` explicitly enables unsafe prompt/response
logging; never enable it for enterprise production.

## Jetson optimization and remaining deployment work

No Jetson/CUDA hardware or production weights were available for this milestone's
CPU verification. A conventional dense 4B model's FP16/BF16 weights alone are
approximately 8 GB before KV cache, runtime, and OS overhead. Gemma 4 E4B has a
larger total text footprint, discussed above. The INT8 linear implementation is
tested on tiny real models, not an assertion that E4B fits Jetson's 8 GB.

Install JetPack-compatible PyTorch, validate the native INT8 GEMM and FP16 kernels, measure actual KV
memory/TTFT under chosen contexts, then implement a dedicated INT4/Q4 or
TensorRT-LLM backend behind `SharedBackboneBackend`. Preserve final hidden access,
exact prompt formatting, one prefill, identical backbone weights, and cache-aware
one-token decoding. Bitsandbytes is neither required nor assumed to work on
Jetson. Verify conversion of the actual licensed 4B checkpoint and tokenizer on
deployment; offline tests cover the supported serialized layouts, not those
unavailable production weights.

Before production federation, connect the secure-aggregation framework, provision
authority public keys, configure approved local data/feature retention, curate a
protected validation corpus, and optionally integrate DP with accounting. Policy,
scanner rules, thresholds, backbone, and LM head are never federated.

## File layout

```text
config/                 routing_policy.yaml, federation.yaml
examples/               synthetic training and validation JSONL
src/edge_router/
  api/                  FastAPI factory, bounded SSE producer
  model/                backend protocol, Gemma/GPT-2 adapters, tiny fixture,
                        decision heads/manager, cache context, sampling, memory
  routing/              deterministic policy, security scanner
  service/              orchestrator, local/handoff executor interfaces
  observability/        structured logs, metrics abstraction
  federation/           adapter/client, packages/verifiers, feature extractor,
                        local trainer, clipped updates, gate, simulation, privacy
  feedback/             provenance schemas, approved-store interface, service
training/               JSONL dataset, head training, evaluation/calibration
scripts/                API launcher, prefill/training benchmarks, FL simulation
tests/                  offline unit/model/API/federation tests; opt-in integration
pyproject.toml
.env.example
```
