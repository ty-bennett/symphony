"""Interactive and JSONL model testing. No automatic downloads or training."""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from time import perf_counter

from edge_router.config import Settings
from edge_router.schemas import (
    Classification,
    DecisionResult,
    GenerationOptions,
    Message,
    StreamEvent,
)
from edge_router.testing.harness import EvaluationCase, ModelTestHarness, summarize
from edge_router.testing.readiness import readiness


def read_profile(path: Path) -> dict[str, str]:
    """Parse literal KEY=VALUE only; never source/execute a shell profile."""
    values = {}
    allowed = {name.upper() for name in Settings.model_fields} | {
        "PUBLIC_CONFIDENCE_FOR_FRONTIER",
        "SENSITIVITY_THRESHOLD",
    }
    with path.open() as stream:
        for index, line in enumerate(stream, 1):
            tokens = shlex.split(line, comments=True)
            if not tokens:
                continue
            if tokens[0] == "export":
                tokens = tokens[1:]
            if len(tokens) != 1 or "=" not in tokens[0]:
                raise ValueError(f"Invalid literal profile setting on line {index}")
            key, value = tokens[0].split("=", 1)
            if key not in allowed or "$" in value or "`" in value:
                raise ValueError(f"Unsupported literal profile setting on line {index}")
            values[key] = value
    return values


def make_settings(args, project: Path) -> tuple[Settings, dict]:
    values = {
        key: value for key, value in os.environ.items() if key.lower() in Settings.model_fields
    }
    thresholds = {
        key: os.environ[key]
        for key in ["PUBLIC_CONFIDENCE_FOR_FRONTIER", "SENSITIVITY_THRESHOLD"]
        if key in os.environ
    }
    if args.profile:
        profile = read_profile(args.profile)
        thresholds.update(
            {
                key: value
                for key, value in profile.items()
                if key in {"PUBLIC_CONFIDENCE_FOR_FRONTIER", "SENSITIVITY_THRESHOLD"}
            }
        )
        values.update(
            {key: value for key, value in profile.items() if key.lower() in Settings.model_fields}
        )
    settings = Settings.model_validate(
        {
            key.lower(): value
            for key, value in values.items()
            if value or key not in {"DECISION_CHECKPOINT", "QUANTIZED_MODEL_PATH"}
        }
    )
    if args.demo:
        settings = settings.model_copy(
            update={
                "model_backend": "tiny",
                "model_id": "offline-tiny-fixture",
                "model_revision": "v1",
                "device": "cpu",
                "dtype": "float32",
                "quantization": "none",
                "quantized_model_path": None,
                "decision_checkpoint": None,
            }
        )
    else:
        if args.device:
            settings.device = args.device
        if args.checkpoint:
            settings.quantized_model_path = str(args.checkpoint.resolve())
        if args.unquantized:
            if args.checkpoint:
                raise ValueError("Choose --checkpoint or --unquantized, not both")
            settings.quantization, settings.quantized_model_path = "none", None
    # Explicitly deny silent content collection, unsafe content logs, and downloads.
    settings.unsafe_content_logging = False
    settings.federation_enabled = False
    settings.local_files_only = not args.allow_download
    for name in [
        "policy_config",
        "strands_prompt_config",
        "quantized_model_path",
        "decision_checkpoint",
        "difficulty_checkpoint",
        "difficulty_policy_config",
    ]:
        value = getattr(settings, name)
        if value and not Path(value).is_absolute():
            setattr(settings, name, str((project / value).resolve()))
    for name in ["model_id", "strands_checkpoint"]:
        value = getattr(settings, name)
        if (project / value).is_dir():
            setattr(settings, name, str((project / value).resolve()))
    return settings, thresholds


def read_cases(path: Path) -> list[EvaluationCase]:
    cases = []
    with path.open() as stream:
        for index, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                cases.append(EvaluationCase.model_validate_json(line))
            except ValueError:
                raise ValueError(f"Invalid evaluation case on line {index}") from None
    if not cases:
        raise ValueError("Evaluation file contains no cases")
    return cases


def display_event(event: StreamEvent, show_response: bool = True):
    if event.event == "decision":
        decision = DecisionResult.model_validate(event.data)
        print(
            f"Route: {decision.route.value} | resolved sensitivity: {decision.resolved_sensitivity.value}"
        )
        for name in ["complexity", "sensitivity"]:
            prediction = getattr(decision, name)
            if prediction is None:
                continue
            probabilities = ", ".join(
                f"{label.value}={value:.3f}" for label, value in prediction.probabilities.items()
            )
            print(f"{name.capitalize()}: {prediction.label.value} ({probabilities})")
        if decision.difficulty:
            print(
                f"Difficulty: {decision.difficulty.score:.4f} (layer {decision.difficulty.layer}; not a probability)"
            )
        print(f"Policy: {decision.policy_reason}")
        if show_response and decision.route.value == "LOCAL":
            print("Local response: ", end="", flush=True)
    elif event.event == "token":
        print(event.data["text"], end="", flush=True)
    elif event.event == "complete":
        if event.data["status"] == "HANDOFF_REQUIRED":
            print("HANDOFF_REQUIRED: no remote request was sent.")
        else:
            print()


def show_timing(outcome):
    record = outcome.safe_record()
    timing = outcome.decision.timing_ms
    print(
        f"Timing: prefill={timing['prefill']:.1f} ms, decision={timing['decision']:.1f} ms, total={outcome.total_ms:.1f} ms"
    )
    if record["time_to_first_token_ms"] is not None:
        print(
            f"TTFT={record['time_to_first_token_ms']:.1f} ms | generated={outcome.generated_tokens} | decode={record['decode_tokens_per_second']:.1f} tokens/s"
        )
    counters = outcome.instrumentation
    print(
        f"Reuse: prefills={counters['prefill_calls']}, prompt tokens={counters['prompt_tokens']}, new decode inputs={counters['decoded_input_tokens']}, verified={outcome.shared_prefill_verified}"
    )
    if outcome.peak_cuda_memory_bytes is not None:
        print(f"Peak CUDA allocation: {outcome.peak_cuda_memory_bytes / 1024**3:.2f} GiB")


def save_report(path: Path, report: dict) -> None:
    # No overwrite, and no inputs/responses in the explicitly requested report.
    with path.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv: list[str] | None = None, project_root: Path | None = None) -> int:
    project = (project_root or Path.cwd()).resolve()
    default_profile = project / ".env.qwen35-difficulty.example"
    if not default_profile.is_file():
        default_profile = project / ".env.gemma4-strands.example"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, default=default_profile if default_profile.is_file() else None
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Offline synthetic fixture, NOT a Gemma/Strands quality test",
    )
    parser.add_argument(
        "--doctor", action="store_true", help="Check readiness without loading any model"
    )
    parser.add_argument("--device", help="Explicit device override, e.g. cpu or cuda:0")
    parser.add_argument(
        "--checkpoint", type=Path, help="Approved local quantized artifact directory (Qwen: quant/)"
    )
    parser.add_argument(
        "--unquantized",
        action="store_true",
        help="Explicitly test the original model instead of INT8",
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow original-model Hub downloads; never needed for a complete INT8 artifact",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--prompt", help="One prompt; avoid secrets in shell history/process arguments"
    )
    group.add_argument(
        "--cases",
        type=Path,
        help="Local JSONL cases, optionally with complexity/sensitivity/route labels",
    )
    parser.add_argument("--mode", choices=["chat", "decision"], default="chat")
    parser.add_argument("--classification", choices=list(Classification), default="UNKNOWN")
    parser.add_argument(
        "--system", help="Optional production system message for interactive/single-prompt mode"
    )
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--top-p", type=float, default=1)
    parser.add_argument(
        "--warmup",
        type=int,
        default=0,
        help="Extra unscored requests before a batch; default is zero",
    )
    parser.add_argument(
        "--output", type=Path, help="Opt-in privacy-safe JSON metrics report; refuses overwrites"
    )
    parser.add_argument(
        "--metadata-logs",
        action="store_true",
        help="Also display structured, content-free application logs",
    )
    args = parser.parse_args(argv)
    try:
        if args.warmup < 0 or (args.warmup and not args.cases):
            raise ValueError("Warmup must be nonnegative and requires --cases")
        if args.output and (args.output.exists() or not (args.cases or args.prompt)):
            raise ValueError("Output must be a new file and requires --cases or --prompt")
        settings, thresholds = make_settings(args, project)
        options = GenerationOptions(
            max_new_tokens=args.max_new_tokens, temperature=args.temperature, top_p=args.top_p
        )
        cases = read_cases(args.cases) if args.cases else []
        report = readiness(settings, args.allow_download)
    except (ValueError, OSError):
        print(
            "Invalid configuration/input. Check profile, JSONL schema, generation limits, and output path; input contents are omitted.",
            file=sys.stderr,
        )
        return 2
    if args.doctor or not report["ready"]:
        print(json.dumps(report, indent=2))
        if not report["ready"]:
            print(
                "Not ready for the requested real model. No download or demo fallback was performed.",
                file=sys.stderr,
            )
        return 0 if report["ready"] else 2
    try:
        from edge_router.api.app import build_orchestrator
        from edge_router.observability.logging import configure_logging
        from edge_router.routing.policy import RoutingPolicy

        configure_logging(settings.log_level if args.metadata_logs else "WARNING")
        start = perf_counter()
        service = build_orchestrator(settings)
        updates = {
            key: float(thresholds[env])
            for env, key in [
                ("PUBLIC_CONFIDENCE_FOR_FRONTIER", "public_confidence_required_for_frontier"),
                ("SENSITIVITY_THRESHOLD", "sensitivity_confidence_threshold"),
            ]
            if env in thresholds
        }
        if updates:
            if not isinstance(service.policy, RoutingPolicy):
                raise ValueError(
                    "Classifier probability thresholds do not apply to regression routing"
                )
            service.policy = RoutingPolicy(
                type(service.policy.config).model_validate(
                    {**service.policy.config.model_dump(), **updates}
                )
            )
        harness = ModelTestHarness(service)
        info = service.backend.model_info()
        print(
            f"Loaded: {info['base_model_id']} | backend={info['backend']} | quantization={info.get('quantization', 'none')} | device={info['device']} | startup={(perf_counter() - start):.2f}s"
        )
        if args.demo:
            print(
                "OFFLINE DEMO: synthetic model and heads. These results do NOT measure Gemma/Strands quality or Jetson speed."
            )
        print(
            "Local-only testing. No training/retention. Responses are displayed in your terminal; saved reports omit content."
        )
    except Exception:
        print(
            "Model loading failed. Check local weights/access, compatible artifact, dependencies, device, and available memory. Exception contents are omitted; no model fallback was used.",
            file=sys.stderr,
        )
        return 2

    def case_for(prompt):
        messages = [Message(role="system", content=args.system)] if args.system else []
        return EvaluationCase(
            messages=[*messages, Message(role="user", content=prompt)],
            security_context={"classification": args.classification},
        )

    def run_case(case):
        outcome = harness.run(
            case,
            options,
            args.mode,
            lambda event: display_event(event, show_response=args.mode == "chat"),
        )
        if args.mode == "decision":
            print("\nDECISION_ONLY: no generation requested.")
        show_timing(outcome)
        return outcome

    if args.prompt:
        try:
            cases = [case_for(args.prompt)]
        except ValueError:
            print("Invalid prompt/system message; content omitted.", file=sys.stderr)
            return 2
    if cases:
        try:
            for _ in range(args.warmup):
                harness.run(cases[0], options, args.mode)
            outcomes = []
            for index, case in enumerate(cases, 1):
                print(f"\nCase {index}/{len(cases)}")
                outcomes.append(run_case(case))
            report = summarize(cases, outcomes, info)
            if args.cases:
                print(
                    "\nSUMMARY\n"
                    + json.dumps(
                        {
                            key: value
                            for key, value in report.items()
                            if key not in {"results", "model"}
                        },
                        indent=2,
                    )
                )
            if args.output:
                save_report(args.output, report)
                print("Privacy-safe metrics report saved; prompts/responses were not included.")
            return (
                1
                if report["routing"]["ground_truth_sensitive_examples_allowed_frontier"]
                or report["routing"]["deterministic_invariant_violations"]
                else 0
            )
        except Exception:
            print(
                "Test request or report failed. Check context/generation limits, artifact/kernel, and output directory. Exception/input contents are omitted.",
                file=sys.stderr,
            )
            return 2
    print(
        "Type a prompt, /info, or /quit. Each prompt is an independent conversation; no history is retained."
    )
    while True:
        try:
            prompt = input("\nYou> ").strip()
            if prompt in {"/quit", "/exit"}:
                return 0
            if prompt == "/info":
                print(json.dumps(info, indent=2))
            elif prompt:
                run_case(case_for(prompt))
        except (EOFError, KeyboardInterrupt):
            print("\nFinished; no prompts/responses were saved.")
            return 0
        except Exception:
            print(
                "Request failed; input/exception contents omitted. Check context limits and model/kernel readiness.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
