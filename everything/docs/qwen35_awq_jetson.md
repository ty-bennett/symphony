# Qwen3.5-4B AWQ on an Orin Nano: standalone smoke test

This tests whether **one real text-only Qwen3.5-4B AWQ model loads and generates on
your Orin GPU**, with a short context and batch size one. No training, Strands
adapter, decision heads, router changes, AWS, or Snowflake. A pass is a small-context
fit/generation check, **not** proof of routing quality, shared-prefill integration,
long-context capacity, or enterprise security readiness.

The harness is standalone: do not install this repository's Torch/Transformers
environment on the Jetson just to run this test. It uses NVIDIA TensorRT Edge-LLM's
native runtime and experimental ONNX-less builder directly. No full FP16 model is
loaded to requantize it; no CPU inference/full-precision/alternate-model fallback.

## 1. Check the device first (no install/download/model allocation)

Copy this repository (or just `scripts/test_qwen35_awq_jetson.py`) to the Jetson.
From the repository root:

```bash
python3 everything/scripts/test_qwen35_awq_jetson.py doctor
```

It reports board, RAM/swap, L4T, Python, CUDA SM/runtime, TensorRT, and missing
runtime prerequisites. Exit 2 / `BLOCKED` is expected before runtime installation.

This particular **published-wheel** test path requires **JetPack 7.2 / L4T 39.2,
CUDA 13.2, Orin SM87, platform TensorRT 10, and CPython 3.10–3.12**. These are the
current [NVIDIA support matrix](https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/getting_started/support-matrix.html)
requirements for the pinned runtime, not requirements inherent to Qwen/AWQ.
**If your device has JetPack 6.x, stop here:** this harness reports that mismatch,
not an unsuccessful model-fit test. It does not install/replace CUDA, reflash the
device, change power settings, or invent a compatible wheel. A JP6-compatible
runtime/source build needs separate verification. The native loader also verifies
the exact platform/ABI match before downloading any weights for `run`.

## 2. Install an isolated, minimal inference environment on a compatible Jetson

Use the JetPack-provided Python/TensorRT installation. Install the matching Python
venv OS package separately if `venv` is unavailable. From the repository root:

```bash
python3 -m venv --system-site-packages .venv-qwen-awq
source .venv-qwen-awq/bin/activate
python -m pip install --upgrade pip
python -m pip install 'tensorrt-edgellm==0.11.0' huggingface_hub
python everything/scripts/test_qwen35_awq_jetson.py doctor
```

`--system-site-packages` exposes JetPack's TensorRT Python bindings; it does not
install TensorRT. The base wheel already includes the native direct builder. No
PyTorch, AutoAWQ, bitsandbytes, vLLM, or vision processor is needed. NVIDIA's
[wheel installation guide](https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/getting_started/installation.html)
documents native payload selection and platform prerequisites.

## 3. Download, build, and run

Prefer an NVMe-backed working directory with ample free space (plan for at least
15–20 GB for weights, runtime installation, and engines; actual engine size varies).
Close memory-heavy applications first. The first command explicitly authorizes a
network download; requests themselves never contact a remote inference service.

```bash
python everything/scripts/test_qwen35_awq_jetson.py run \
  --download --work-dir artifacts/qwen35-4b-awq \
  --show-output --report qwen35-awq-first-run.json

# Subsequent runs are offline, reusing the matching locally built engine.
python everything/scripts/test_qwen35_awq_jetson.py run \
  --work-dir artifacts/qwen35-4b-awq \
  --show-output --report qwen35-awq-warm-run.json
```

You can replace `--work-dir` with an absolute NVMe path. Downloads are resumable,
pinned to `QuantTrio/Qwen3.5-4B-AWQ` revision
`32c292e3a73afe1138518180b1b6d2868c980ee2`. Only Safetensors and tokenizer/config/
license artifacts are downloaded, never Python remote code or pickle weights.
The approximately **6 GB download is mixed precision**: the checkpoint excludes
vision, attention/DeltaNet, and its first layer from AWQ. Do **not** assume all 4B
parameters occupy four bits or that runtime RAM is only 2 GB. See the actual
[checkpoint configuration](https://huggingface.co/QuantTrio/Qwen3.5-4B-AWQ/blob/32c292e3a73afe1138518180b1b6d2868c980ee2/config.json).
Vision weights may share downloaded shards with text weights, but `--components llm`
builds/loads **no vision engine**. AWQ is **INT4 weights / FP16 activations (W4A16)**
for quantized projections; other modules remain higher precision. It is not W8A8.

To download on a separate machine, install `huggingface_hub`, run the following,
then copy the complete resulting checkpoint directory to the Jetson:

```bash
python everything/scripts/test_qwen35_awq_jetson.py download \
  --work-dir artifacts/qwen35-4b-awq

# On the Jetson, with the native runtime installed:
python everything/scripts/test_qwen35_awq_jetson.py run \
  --model-dir /absolute/path/to/copied/checkpoint \
  --work-dir artifacts/qwen35-4b-awq --show-output
```

## What is measured?

Defaults: maximum input 512 tokens, KV capacity 1024, maximum generation 64 tokens,
batch one, greedy decoding, thinking disabled. One cold request and two warm
requests use the same loaded model. Initial engine compilation may take minutes;
it is timed separately and matching subsequent runs do not rebuild it.

The content-free JSON report includes:

- `status: PASS` only after real generated text and token IDs are returned on all runs.
- Weight-file bytes, quantization recipe, platform versions, engine build/reuse and load time.
- Cold/warm request latency, generated-token counts, **end-to-end** tokens/second.
- Sampled peak **system** RAM use, swap use, process peak RSS, CUDA free/total snapshots.

The blocking native API does not measure time to first token or peak CUDA allocator
usage: these fields are `null`, not fabricated. End-to-end throughput includes
prompt prefill and is not decode-only throughput. Jetson uses unified RAM; do not
add CUDA and system figures. System memory includes unrelated processes, and swap
activity is worth inspecting before claiming the workload fits comfortably in RAM.
No prompt, response, token sequence, or raw exception contents are saved in the
report. `--show-output` deliberately prints the response on the terminal. Use only
public smoke-test prompts; custom `--prompt` also appears in shell history/process
arguments, and native SDK logging is outside this application's production logger.
Report files are exclusive-create, never overwritten.

## Failure interpretation / smaller profile

Exit 2 reports the failing stage: preflight, download, checkpoint validation, engine
build, model load, or generation. Missing/incompatible software is not a memory-fit
result. Missing AWQ support/numeric errors are not fixed by silently running FP16.
The [direct builder](https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/getting_started/direct-engine-builder.html)
is explicitly **experimental**; NVIDIA lists AWQ support as implemented but not yet
covered by direct-builder L0 CI. This specific checkpoint + Orin combination is
therefore an on-device experiment, not a claimed prevalidated deployment.

If native diagnostics specifically show memory exhaustion, try a smaller profile:

```bash
python everything/scripts/test_qwen35_awq_jetson.py run \
  --work-dir artifacts/qwen35-4b-awq \
  --max-input-len 256 --max-kv-cache-capacity 512 --max-new-tokens 32 \
  --show-output --report qwen35-awq-small-context.json
```

Changing profile/runtime/checkpoint creates a different engine; the old one is
preserved. Failed builds are preserved too, with no success receipt. If Linux
OOM-kills Python, a final JSON report may not be written: `Killed`/exit 137 is **not
a pass**; inspect system OOM diagnostics locally. No automatic swap configuration,
CPU offloading, alternate quantization, or OS upgrades are performed.

The development workstation has no Jetson runtime. Unit tests use fake native
bindings to test orchestration, failure handling, quantization checks, engine
reuse, and report privacy; **they do not establish GPU execution or model quality**.
