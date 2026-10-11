"""Small real Qwen + difficulty-head CUDA test. No downloads or remote calls."""

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))


def run_test(service, max_new_tokens: int, exercise_local: bool = False) -> dict:
    import torch

    from edge_router.model.memory import cache_bytes, component_memory
    from edge_router.schemas import GenerationOptions, Message, Route, SecurityContext

    backend = service.backend
    snapshot = backend.head_manager.snapshot()
    memory = component_memory(
        {
            "backbone": backend.backbone,
            "lm_head": backend.lm_head,
            "difficulty_head": snapshot.heads,
        }
    )
    trace = []
    hook = backend.backbone.register_forward_pre_hook(
        lambda module, args, kwargs: trace.append(kwargs["input_ids"].shape[1]),
        with_kwargs=True,
    )
    before = backend.instrumentation.snapshot()
    pieces, count = [], 0
    started = perf_counter()
    try:
        with backend.execution_lock, torch.inference_mode():
            messages = [Message(role="user", content="Reply with a short greeting.")]
            flags = service.scanner.scan(messages)
            context = backend.prefill(messages)
            prediction = backend.classify(context, snapshot)
            decision = service.policy.decide(prediction, flags, SecurityContext())
            cache_size = cache_bytes(context.past_key_values)
            original_cache = context.past_key_values
            decode = decision.route == Route.LOCAL or exercise_local
            if decode:
                for token in backend.decode_from_prefill(
                    context, GenerationOptions(max_new_tokens=max_new_tokens)
                ):
                    count += 1
                    pieces.append(token.text)
    finally:
        hook.remove()
    after = backend.instrumentation.snapshot()
    expected = [context.prompt_length] + [1] * max(count - 1, 0)
    reused = (
        trace == expected
        and after["prefill_calls"] - before["prefill_calls"] == 1
        and after["prompt_tokens"] - before["prompt_tokens"] == context.prompt_length
        and after["decoded_input_tokens"] - before["decoded_input_tokens"] == max(count - 1, 0)
        and context.past_key_values is original_cache
        and after["backbone_instances"] == 1
    )
    if not reused or (decode and count == 0):
        raise RuntimeError("Shared-prefill/generation test failed")
    return {
        "status": "PASS" if decode else "PASS_ROUTING_ONLY",
        "route": decision.route.value,
        "difficulty_score": prediction.score,
        "decision_model_version": snapshot.version,
        "diagnostic_local_override": decision.route != Route.LOCAL and exercise_local,
        "shared_prefill_verified": reused,
        "prompt_tokens": context.prompt_length,
        "generated_tokens": count,
        "cache_after_prefill_bytes": cache_size,
        "cache_after_decode_bytes": cache_bytes(context.past_key_values),
        "component_memory": memory,
        "elapsed_seconds": perf_counter() - started,
        # Display only, excluded from the saved report below.
        "response": "".join(pieces),
    }


def main(argv=None) -> int:
    import torch

    from edge_router.api.app import build_orchestrator
    from edge_router.observability.logging import configure_logging
    from edge_router.testing.cli import make_settings
    from edge_router.testing.readiness import readiness

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=PROJECT / ".env.qwen35-difficulty.example")
    parser.add_argument(
        "--checkpoint", type=Path, help="Exact quant/ export from router_heads/README.md"
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-context-tokens", type=int, default=256)
    parser.add_argument("--max-new-tokens", type=int, default=16)
    parser.add_argument(
        "--doctor", action="store_true", help="Check readiness without loading the backbone"
    )
    parser.add_argument(
        "--exercise-local",
        action="store_true",
        help="Explicit diagnostic: test cached local decode even if routing recommends AWS; never contacts AWS",
    )
    parser.add_argument(
        "--show-response",
        action="store_true",
        help="Display generated text in the terminal, never in the JSON report",
    )
    parser.add_argument(
        "--output", type=Path, help="Optional content-free JSON report; refuses overwrites"
    )
    parser.set_defaults(demo=False, allow_download=False, unquantized=False)
    args = parser.parse_args(argv)
    stage = "configuration"
    report = {}
    try:
        if args.output and (args.output.exists() or not args.output.parent.is_dir()):
            raise ValueError("Report must be a new file in an existing directory")
        if not 1 <= args.max_new_tokens < args.max_context_tokens <= 8192:
            raise ValueError("Invalid context/generation limits")
        settings, _ = make_settings(args, PROJECT)
        settings.max_context_tokens = args.max_context_tokens
        if settings.model_backend != "qwen35_difficulty" or not settings.device.startswith("cuda"):
            raise ValueError("This real-model smoke test requires the Qwen difficulty CUDA profile")
        stage = "readiness"
        report = readiness(settings)
        for blocker in report["blockers"]:
            if blocker["code"] == "CUDA_UNAVAILABLE":
                blocker["action"] = (
                    "Activate a JetPack-compatible CUDA PyTorch environment. This device test does not permit a CPU fallback."
                )
        if args.doctor or not report["ready"]:
            print(json.dumps(report, indent=2))
            return 0 if report["ready"] else 2
        device = torch.device(settings.device)
        configure_logging("WARNING")
        stage = "model_load"
        print(
            "Loading the exact packed AWQ backbone and trained head (local files only)...",
            flush=True,
        )
        service = build_orchestrator(settings)
        torch.cuda.synchronize(device)
        print("Model loaded; running one prefill + decision + cache-aware decode...", flush=True)
        torch.cuda.reset_peak_memory_stats(device)
        stage = "inference"
        report = run_test(service, args.max_new_tokens, args.exercise_local)
        response = report.pop("response")
        torch.cuda.synchronize(device)
        free, total = torch.cuda.mem_get_info(device)
        report["cuda"] = {
            "device_name": torch.cuda.get_device_name(device),
            "allocated_bytes": torch.cuda.memory_allocated(device),
            "reserved_bytes": torch.cuda.memory_reserved(device),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
            "free_bytes": free,
            "total_bytes": total,
        }
        print(
            f"{report['status']}: route={report['route']}, difficulty={report['difficulty_score']:.4f}"
        )
        for name, values in report["component_memory"]["components"].items():
            print(
                f"  {name}: {values['resident_bytes'] / 2**20:.2f} MiB resident; {values['additional_bytes'] / 2**20:.2f} MiB additional"
            )
        print(
            f"  Total unique weights/buffers: {report['component_memory']['total_unique_bytes'] / 2**20:.2f} MiB"
        )
        print(f"  Cache after prefill: {report['cache_after_prefill_bytes'] / 2**20:.2f} MiB")
        print(
            f"  Peak CUDA allocated during inference: {report['cuda']['peak_allocated_bytes'] / 2**20:.2f} MiB"
        )
        print(f"  CUDA reserved: {report['cuda']['reserved_bytes'] / 2**20:.2f} MiB")
        print(
            f"  Generated tokens: {report['generated_tokens']}; shared prefill verified: {report['shared_prefill_verified']}"
        )
        print("LM weights are tied to embeddings: use 'additional' bytes to avoid double-counting.")
        print(
            "Resident sizes exclude activations; overall peak includes cache and temporary allocations. Jetson GPU/CPU share RAM."
        )
        if report["diagnostic_local_override"]:
            print(
                "Explicit diagnostic local decode was exercised; the AWS routing recommendation was not changed."
            )
        if report["status"] == "PASS_ROUTING_ONLY":
            print(
                "LM generation was NOT tested. Rerun with --exercise-local to test it explicitly."
            )
        if args.show_response and report["generated_tokens"]:
            print(f"Local response: {response}")
    except Exception as exc:
        code = (
            "CUDA_OUT_OF_MEMORY" if isinstance(exc, torch.cuda.OutOfMemoryError) else "TEST_FAILED"
        )
        report = {"status": "FAIL", "stage": stage, "error_code": code}
        print(json.dumps(report, indent=2))
        print(
            "Check the doctor output, exact checkpoint, CUDA PyTorch/Transformers installation, and free RAM. No fallback/download occurred."
        )
    if args.output:
        try:
            with args.output.open("x") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
                stream.write("\n")
            print(f"Report: {args.output}")
        except OSError:
            print("Could not save report (no file was overwritten).")
            return 2
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
