#!/usr/bin/env python3
"""Standalone, opt-in Qwen3.5-4B INT4 AWQ smoke test. No router/PyTorch dependency.

Uses NVIDIA's experimental checkpoint-direct builder and native TensorRT runtime.
Only ``run --download`` or ``download`` contacts Hugging Face. Never falls back to
CPU, an FP16 checkpoint, another model, or a vision engine. Python 3.10 compatible.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import importlib.metadata
import json
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

MODEL_ID = "QuantTrio/Qwen3.5-4B-AWQ"
MODEL_REVISION = "32c292e3a73afe1138518180b1b6d2868c980ee2"
RUNTIME_VERSION = "0.11.0"
DEFAULT_PROMPT = "Explain in three short sentences why keeping private data on-device is useful."
ARTIFACTS = [
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "chat_template.jinja",
    "vocab.json",
    "merges.txt",
    "LICENSE",
    "*.safetensors",
    "model.safetensors.index.json",
]
SUPPORT_URL = (
    "https://nvidia.github.io/TensorRT-Edge-LLM/latest/"
    "user_guide/getting_started/support-matrix.html"
)


class SmokeTestError(RuntimeError):
    """An actionable diagnostic safe to include in the content-free report."""


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text().strip().strip("\x00")
    except OSError:
        return ""


def memory_snapshot() -> dict[str, int]:
    fields = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            fields[key] = int(value.split()[0]) * 1024
    return fields


class CudaProbe:
    """Query the installed CUDA runtime without importing/loading PyTorch."""

    def __init__(self) -> None:
        library = ctypes.util.find_library("cudart")
        candidates = [library] if library else []
        candidates.extend(
            [
                "/usr/local/cuda/lib64/libcudart.so",
                "/usr/local/cuda/targets/aarch64-linux/lib/libcudart.so",
            ]
        )
        for candidate in candidates:
            try:
                self.library = ctypes.CDLL(candidate)
                break
            except OSError:
                continue
        else:
            raise SmokeTestError(
                "CUDA runtime library unavailable; check the JetPack installation."
            )

    def integer(self, function: str, *arguments: int) -> int:
        value = ctypes.c_int()
        status = getattr(self.library, function)(ctypes.byref(value), *arguments)
        if status:
            raise SmokeTestError(f"{function} failed with CUDA error code {status}.")
        return value.value

    def info(self) -> dict[str, Any]:
        if self.integer("cudaGetDeviceCount") < 1:
            raise SmokeTestError("No CUDA device is available; CPU fallback is forbidden.")
        major = self.integer("cudaDeviceGetAttribute", 75, 0)
        minor = self.integer("cudaDeviceGetAttribute", 76, 0)
        return {
            "runtime_version": self.integer("cudaRuntimeGetVersion"),
            "compute_capability": f"{major}.{minor}",
            **self.memory(),
        }

    def memory(self) -> dict[str, int]:
        free, total = ctypes.c_size_t(), ctypes.c_size_t()
        status = self.library.cudaMemGetInfo(ctypes.byref(free), ctypes.byref(total))
        if status:
            raise SmokeTestError(f"cudaMemGetInfo failed with CUDA error code {status}.")
        return {"free_bytes": free.value, "total_bytes": total.value}


def platform_blockers(info: dict[str, Any]) -> list[str]:
    """Restrict this harness to the published 0.11.0 Orin wheel's supported row."""
    blockers = []
    if info["machine"] not in {"aarch64", "arm64"} or "orin" not in info["board"].lower():
        blockers.append("Run this test on a Jetson Orin, not this workstation/other GPU.")
    if info["python"] not in {"3.10", "3.11", "3.12"}:
        blockers.append("The runtime wheel needs CPython 3.10, 3.11, or 3.12.")
    if not re.search(r"R39\s*\(release\).*REVISION:\s*2(?:\D|$)", info["l4t"]):
        blockers.append(
            "This runtime path requires JetPack 7.2 / L4T 39.2. JetPack 6.x is not "
            "supported by this wheel; that does not prove the model cannot fit."
        )
    cuda = info.get("cuda", {})
    if cuda.get("compute_capability") != "8.7" or cuda.get("runtime_version") != 13020:
        blockers.append("Requires the Orin SM87 / JetPack CUDA 13.2 stack.")
    if not info.get("tensorrt", "").startswith("10."):
        blockers.append("Install/expose the JetPack TensorRT 10 Python bindings in this venv.")
    if info.get("edge_llm") != RUNTIME_VERSION:
        blockers.append(f"Install tensorrt-edgellm=={RUNTIME_VERSION} in a separate venv.")
    if not info.get("builder"):
        blockers.append("tensorrt-edgellm-build is missing from the active venv's PATH.")
    return blockers


def doctor() -> dict[str, Any]:
    info: dict[str, Any] = {
        "machine": platform.machine(),
        "board": read_text("/proc/device-tree/model"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "l4t": read_text("/etc/nv_tegra_release"),
        "memory": memory_snapshot(),
        "support_matrix": SUPPORT_URL,
        "builder": shutil.which("tensorrt-edgellm-build"),
    }
    for key, distribution in [("tensorrt", "tensorrt"), ("edge_llm", "tensorrt-edgellm")]:
        try:
            info[key] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            info[key] = ""
    # Some platform installations expose bindings without a tensorrt dist-info entry.
    if not info["tensorrt"] and info["machine"] in {"aarch64", "arm64"}:
        try:
            import tensorrt

            info["tensorrt"] = tensorrt.__version__
        except Exception:
            pass
    try:
        info["cuda"] = CudaProbe().info()
    except Exception as error:
        info["cuda"] = {}
        info["cuda_probe_error_type"] = type(error).__name__
    info["blockers"] = platform_blockers(info)
    if not info["blockers"]:
        try:
            load_native()  # The wheel's exact payload selector is authoritative.
        except Exception as error:
            info["native_error_type"] = type(error).__name__
            info["blockers"].append(
                "Native payload selection/load failed; verify the exact CUDA/TensorRT/"
                "JetPack stack with the NVIDIA support matrix. No weights were loaded."
            )
    info["ready"] = not info["blockers"]
    return info


def load_native() -> Any:
    from tensorrt_edgellm import runtime

    return runtime.load()


def download_checkpoint(model_dir: Path) -> None:
    from huggingface_hub import snapshot_download

    model_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=str(model_dir),
        allow_patterns=ARTIFACTS,
        max_workers=2,
    )


def inspect_checkpoint(model_dir: Path) -> dict[str, Any]:
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        raise SmokeTestError("Missing local checkpoint; use run --download or --model-dir PATH.")
    config = json.loads(config_path.read_text())
    text = config.get("text_config", {})
    quant = config.get("quantization_config", {})
    if (
        config.get("model_type") != "qwen3_5"
        or text.get("hidden_size") != 2560
        or text.get("num_hidden_layers") != 32
        or text.get("intermediate_size") != 9216
    ):
        raise SmokeTestError("Checkpoint is not the expected dense Qwen3.5-4B architecture.")
    if (
        quant.get("quant_method") != "awq"
        or quant.get("bits") != 4
        or quant.get("version", "").lower() != "gemm"
        or quant.get("group_size") != 128
        or quant.get("zero_point") is not True
    ):
        raise SmokeTestError(
            "Requires INT4 AWQ GEMM, group_size=128, zero_point=true; no fallback."
        )
    weights = sorted(model_dir.glob("*.safetensors"))
    if not weights:
        raise SmokeTestError("No Safetensors weights found; FP16/pickle fallback is forbidden.")
    index_path = model_dir / "model.safetensors.index.json"
    if index_path.exists():
        required = set(json.loads(index_path.read_text())["weight_map"].values())
        if not required.issubset({path.name for path in weights}):
            raise SmokeTestError(
                "Checkpoint download is incomplete: missing indexed weight shards."
            )
    for filename in ["tokenizer.json", "tokenizer_config.json"]:
        if not (model_dir / filename).is_file():
            raise SmokeTestError("Checkpoint is missing tokenizer artifacts.")
    # Native build/runtime verifies provider tensor identities. This digest only keys profiles.
    identity = hashlib.sha256(config_path.read_bytes())
    for path in weights:
        identity.update(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}".encode())
    return {
        "identity": identity.hexdigest(),
        "weight_file_bytes": sum(path.stat().st_size for path in weights),
        "quantization": quant,
        "precision": "INT4 AWQ projections / FP16 activations; excluded modules remain higher precision",
        "vision_engine_loaded": False,
    }


def build_command(model_dir: Path, engine_dir: Path, args: argparse.Namespace) -> list[str]:
    builder = shutil.which("tensorrt-edgellm-build")
    if not builder:
        raise SmokeTestError("Missing tensorrt-edgellm-build executable.")
    return [
        builder,
        "--model-dir",
        str(model_dir),
        "--engine-dir",
        str(engine_dir),
        "--components",
        "llm",
        "--max-batch-size",
        "1",
        "--max-input-len",
        str(args.max_input_len),
        "--max-kv-cache-capacity",
        str(args.max_kv_cache_capacity),
    ]


def ensure_engine(
    model_dir: Path,
    args: argparse.Namespace,
    info: dict[str, Any],
    checkpoint: dict[str, Any],
) -> tuple[Path, float, bool]:
    manifest = {
        "checkpoint_identity": checkpoint["identity"],
        "runtime": RUNTIME_VERSION,
        "l4t": info["l4t"],
        "cuda": info["cuda"]["runtime_version"],
        "sm": info["cuda"]["compute_capability"],
        "tensorrt": info["tensorrt"],
        "max_input_len": args.max_input_len,
        "max_kv_cache_capacity": args.max_kv_cache_capacity,
        "components": ["llm"],
        "max_batch_size": 1,
    }
    key = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:20]
    engine_dir = args.work_dir / "engines" / key
    candidates = [engine_dir, *sorted(engine_dir.parent.glob(f"{key}-retry-*"))]
    for candidate in candidates:
        receipt = candidate / "smoke-test-manifest.json"
        if (candidate / "llm.engine").is_file() and receipt.is_file():
            try:
                if json.loads(receipt.read_text()) == manifest:
                    return candidate, 0.0, True
            except (OSError, ValueError):
                pass  # Preserve invalid receipts; a fresh build cannot reuse them.
    if engine_dir.exists():
        # Keep failed/unknown builds; never delete or overwrite an arbitrary user's engine directory.
        engine_dir = Path(tempfile.mkdtemp(prefix=f"{key}-retry-", dir=engine_dir.parent))
    else:
        engine_dir.mkdir(parents=True)
    started = time.perf_counter()
    print(
        "Building a text-only engine (experimental AWQ path); first build can take minutes.",
        file=sys.stderr,
        flush=True,
    )
    result = subprocess.run(build_command(model_dir, engine_dir, args), check=False)
    if result.returncode or not (engine_dir / "llm.engine").is_file():
        raise SmokeTestError(
            f"Engine build failed (exit {result.returncode}); inspect the native builder diagnostics. "
            "This may be an AWQ/frontend/kernel incompatibility or memory exhaustion, not proof "
            "the model cannot fit. No fallback was attempted."
        )
    (engine_dir / "smoke-test-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return engine_dir, time.perf_counter() - started, False


class MemoryMonitor:
    """Sample system RAM, including native builder activity; not a CUDA allocation profiler."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.peak_used_bytes = 0
        self.peak_swap_used_bytes = 0
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self) -> None:
        while True:
            fields = memory_snapshot()
            self.peak_used_bytes = max(
                self.peak_used_bytes,
                fields.get("MemTotal", 0) - fields.get("MemAvailable", 0),
            )
            self.peak_swap_used_bytes = max(
                self.peak_swap_used_bytes,
                fields.get("SwapTotal", 0) - fields.get("SwapFree", 0),
            )
            if self.stop.wait(0.1):
                return

    def __enter__(self) -> MemoryMonitor:
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop.set()
        self.thread.join()


def generate(
    native: Any,
    llm: Any,
    prompt: str,
    max_new_tokens: int,
    show_output: bool,
) -> dict[str, Any]:
    request = native.create_generation_request(
        [[native.create_text_message("user", prompt)]],
        temperature=0.0,
        max_generate_length=max_new_tokens,
        enable_thinking=False,
        apply_chat_template=True,
        add_generation_prompt=True,
    )
    started = time.perf_counter()
    response = llm.handle_request(request)
    elapsed = time.perf_counter() - started
    if (
        len(response.output_texts) != 1
        or not response.output_texts[0].strip()
        or len(response.output_ids) != 1
        or not response.output_ids[0]
    ):
        raise SmokeTestError("Inference returned no generated text/tokens; test did not pass.")
    count = len(response.output_ids[0])
    if show_output:
        print(
            "\n--- Generated response (console only, not saved in the report) ---", file=sys.stderr
        )
        print(response.output_texts[0], file=sys.stderr, flush=True)
    return {
        "generated_tokens": count,
        "elapsed_seconds": elapsed,
        "end_to_end_tokens_per_second": count / elapsed,
        "ttft_seconds": None,  # Blocking API: do not mislabel response latency as TTFT.
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "download", "run"])
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/qwen35-4b-awq"))
    parser.add_argument("--model-dir", type=Path, help="Existing local AWQ Safetensors checkpoint.")
    parser.add_argument(
        "--download", action="store_true", help="Explicitly download pinned weights."
    )
    parser.add_argument("--max-input-len", type=int, default=512)
    parser.add_argument("--max-kv-cache-capacity", type=int, default=1024)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument(
        "--runs", type=int, default=2, help="Measured warm runs after one cold request."
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Use PUBLIC test text only.")
    parser.add_argument(
        "--show-output", action="store_true", help="Display response on console only."
    )
    parser.add_argument(
        "--report", type=Path, help="Optional content-free JSON report (exclusive create)."
    )
    args = parser.parse_args(argv)
    if min(args.max_input_len, args.max_kv_cache_capacity, args.max_new_tokens, args.runs) < 1:
        parser.error("Token limits and runs must be positive.")
    if args.max_input_len + args.max_new_tokens > args.max_kv_cache_capacity:
        parser.error("max-input-len + max-new-tokens must not exceed max-kv-cache-capacity.")
    if args.model_dir and (args.download or args.command == "download"):
        parser.error("Do not combine an existing --model-dir with a download.")
    args.work_dir = args.work_dir.resolve()
    if args.model_dir:
        args.model_dir = args.model_dir.resolve()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report: dict[str, Any] = {"status": "FAILED", "stage": "preflight"}
    exit_code = 2
    try:
        if args.report and args.report.exists():
            raise SmokeTestError(
                "Report already exists; select another filename (never overwritten)."
            )
        if args.command == "doctor":
            report.update(doctor())
            report["status"] = "READY" if report["ready"] else "BLOCKED"
            exit_code = 0 if report["ready"] else 2
        else:
            if args.command == "run":
                info = doctor()
                report["platform"] = info
                if not info["ready"]:
                    raise SmokeTestError(
                        "Preflight blocked; resolve platform.blockers before downloading."
                    )
            model_dir = args.model_dir or args.work_dir / "checkpoint"
            report["model"] = {
                "id": MODEL_ID if not args.model_dir else "local-checkpoint",
                "revision": MODEL_REVISION if not args.model_dir else None,
            }
            if args.command == "download" or args.download:
                report["stage"] = "download"
                print(
                    "Downloading the pinned AWQ checkpoint (~6 GB); no training/calibration.",
                    file=sys.stderr,
                    flush=True,
                )
                download_checkpoint(model_dir)
            report["stage"] = "checkpoint_validation"
            checkpoint = inspect_checkpoint(model_dir)
            report["checkpoint"] = checkpoint
            if args.command == "download":
                report.update(status="DOWNLOADED", model_dir=str(model_dir))
                exit_code = 0
            else:
                with MemoryMonitor() as monitor:
                    report["stage"] = "engine_build"
                    engine_dir, seconds, reused = ensure_engine(model_dir, args, info, checkpoint)
                    report["engine"] = {
                        "directory": str(engine_dir),
                        "build_seconds": seconds,
                        "reused": reused,
                    }
                    report["stage"] = "model_load"
                    cuda = CudaProbe()
                    report["cuda_before_load"] = cuda.memory()
                    native = load_native()
                    started = time.perf_counter()
                    llm = native.LLMRuntime(
                        engine_dir=str(engine_dir),
                        checkpoint_dir=str(model_dir),
                    )
                    report["load_seconds"] = time.perf_counter() - started
                    report["cuda_after_load"] = cuda.memory()
                    report["stage"] = "generation"
                    report["cold_request"] = generate(
                        native,
                        llm,
                        args.prompt,
                        args.max_new_tokens,
                        args.show_output,
                    )
                    report["warm_requests"] = [
                        generate(native, llm, args.prompt, args.max_new_tokens, False)
                        for _ in range(args.runs)
                    ]
                    report["cuda_after_generation"] = cuda.memory()
                report["memory"] = {
                    "sampled_peak_system_used_bytes": monitor.peak_used_bytes,
                    "sampled_peak_system_swap_used_bytes": monitor.peak_swap_used_bytes,
                    "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * 1024,
                    "peak_cuda_allocation_bytes": None,
                    "note": "Jetson RAM is unified. System use includes other processes; do not sum "
                    "it with CUDA memory. CUDA snapshots are not peak allocator measurements.",
                }
                report.update(status="PASS", stage="complete", max_new_tokens=args.max_new_tokens)
                exit_code = 0
    except Exception as error:
        # Library exceptions can contain credentials or input text. Never echo their raw contents.
        report["error_type"] = type(error).__name__
        report["error"] = (
            str(error)
            if isinstance(error, SmokeTestError)
            else (
                f"{report['stage']} failed; check local SDK/checkpoint/runtime installation. "
                "No CPU or full-precision fallback was attempted."
            )
        )
    print(json.dumps(report, indent=2), flush=True)
    if args.report and not args.report.exists():
        with args.report.open("x") as handle:
            handle.write(json.dumps(report, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
