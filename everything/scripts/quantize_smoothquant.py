"""Offline, head-label-free SmoothQuant calibration using approved LOCAL JSONL."""

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

import torch


def calibration_examples(path: Path):
    from edge_router.schemas import Message

    with path.open() as stream:
        for index, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                messages = [Message.model_validate(value) for value in record["messages"]]
            except (ValueError, KeyError, TypeError):
                # Never include decoder/Pydantic exceptions or raw line text.
                raise ValueError(f"Invalid local calibration record at line {index}") from None
            yield messages


def main():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "src"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="Literal env profile; never shell-executed")
    parser.add_argument("--device", help="Explicit CPU/CUDA device override")
    parser.add_argument(
        "--hub-cache", type=Path, help="Local Hub cache containing original weights"
    )
    parser.add_argument("--calibration-jsonl", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--max-examples", type=int, default=128)
    args = parser.parse_args()
    if args.hub_cache:
        os.environ["HF_HUB_CACHE"] = str(args.hub_cache.resolve())
    from edge_router.config import Settings
    from edge_router.model.gemma4_strands_backend import build_gemma4_strands_backend
    from edge_router.model.quantization.checkpoint import save_quantized_backend
    from edge_router.model.quantization.gemma4 import quantize_backend
    from edge_router.testing.cli import read_profile

    if not args.calibration_jsonl.is_file() or args.output.exists():
        parser.error("Calibration file must exist and output must be a new directory")
    if not 0 <= args.alpha <= 1 or args.max_examples < 1:
        parser.error("Alpha must be between zero and one and max-examples must be positive")
    # Calibration is a separate process; serving only loads the exported INT8 artifact.
    settings = Settings.from_env()
    if args.profile:
        profile = read_profile(args.profile)
        settings = Settings.model_validate(
            {
                **settings.model_dump(),
                **{
                    name.lower(): value
                    for name, value in profile.items()
                    if name.lower() in Settings.model_fields
                    and (value or name != "DECISION_CHECKPOINT")
                },
            }
        )
        for name in ["policy_config", "strands_prompt_config"]:
            value = getattr(settings, name)
            if not Path(value).is_absolute():
                setattr(settings, name, str((project / value).resolve()))
    if args.device:
        settings.device = args.device
    settings = settings.model_copy(
        update={"quantization": "none", "quantized_model_path": None, "decision_checkpoint": None}
    )
    if settings.model_backend != "gemma4_strands":
        parser.error("This converter requires MODEL_BACKEND=gemma4_strands")
    start = perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    print("Loading original text backbone and the frozen Strands adapter...", file=sys.stderr)
    backend = build_gemma4_strands_backend(settings)
    print("Running local forward-only calibration and INT8 conversion...", file=sys.stderr)
    recipe = quantize_backend(
        backend, calibration_examples(args.calibration_jsonl), args.alpha, args.max_examples
    )
    print("Saving the immutable INT8 artifact and integrity manifest...", file=sys.stderr)
    destination = save_quantized_backend(backend, recipe, args.output)
    print(
        json.dumps(
            {
                "artifact": str(destination),
                "artifact_bytes": sum(
                    path.stat().st_size for path in destination.iterdir() if path.is_file()
                ),
                "conversion_seconds": perf_counter() - start,
                "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated()
                if torch.cuda.is_available()
                else None,
                **{k: v for k, v in recipe.items() if k != "linear_modules"},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
