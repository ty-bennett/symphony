"""Offline, head-label-free SmoothQuant calibration using approved LOCAL JSONL."""

import argparse
import json
from pathlib import Path
from time import perf_counter

import torch

from edge_router.config import Settings
from edge_router.model.gemma4_strands_backend import build_gemma4_strands_backend
from edge_router.model.quantization.checkpoint import save_quantized_backend
from edge_router.model.quantization.gemma4 import quantize_backend
from edge_router.schemas import Message


def calibration_examples(path: Path):
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-jsonl", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--max-examples", type=int, default=128)
    args = parser.parse_args()
    if not args.calibration_jsonl.is_file() or args.output.exists():
        parser.error("Calibration file must exist and output must be a new directory")
    if not 0 <= args.alpha <= 1 or args.max_examples < 1:
        parser.error("Alpha must be between zero and one and max-examples must be positive")
    # Calibration is a separate process; serving only loads the exported INT8 artifact.
    settings = Settings.from_env().model_copy(
        update={"quantization": "none", "quantized_model_path": None, "decision_checkpoint": None}
    )
    if settings.model_backend != "gemma4_strands":
        parser.error("This converter requires MODEL_BACKEND=gemma4_strands")
    start = perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    backend = build_gemma4_strands_backend(settings)
    recipe = quantize_backend(
        backend, calibration_examples(args.calibration_jsonl), args.alpha, args.max_examples
    )
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
