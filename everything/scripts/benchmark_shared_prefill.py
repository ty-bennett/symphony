"""Compare two prefills with one prefill using the same single model instance."""

import argparse
import json
from time import perf_counter

import torch

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings
from edge_router.model.memory import cache_bytes, synchronize
from edge_router.schemas import GenerationOptions, Message


def measure(backend, messages, options, naive):
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    before = backend.instrumentation.snapshot()
    synchronize()
    start = perf_counter()
    context = backend.prefill(messages)
    synchronize()
    prefill = perf_counter() - start
    t = perf_counter()
    backend.classify(context, backend.head_manager.snapshot())
    synchronize()
    decision = perf_counter() - t
    kv_memory = cache_bytes(context.past_key_values)
    tokens = context.prompt_length
    if naive:
        del context
        t = perf_counter()
        context = backend.prefill(messages)
        synchronize()
        prefill += perf_counter() - t
    count, first = 0, None
    decode_start = perf_counter()
    for token in backend.decode_from_prefill(context, options):
        synchronize()
        count += 1
        if first is None:
            first = perf_counter()
    end = perf_counter()
    after = backend.instrumentation.snapshot()
    return {
        "path": "naive" if naive else "shared",
        "prompt_tokens": tokens,
        "prefill_ms": prefill * 1000,
        "decision_head_ms": decision * 1000,
        "time_to_first_token_ms": (first - start) * 1000,
        "tokens_per_second": count / max(end - decode_start, 1e-9),
        "total_ms": (end - start) * 1000,
        "generated_tokens": count,
        "kv_cache_bytes": kv_memory,
        "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated()
        if torch.cuda.is_available()
        else None,
        "prefill_calls": after["prefill_calls"] - before["prefill_calls"],
        "prompt_tokens_processed": after["prompt_tokens"] - before["prompt_tokens"],
        "decode_input_tokens": after["decoded_input_tokens"] - before["decoded_input_tokens"],
        "backbone_instances": after["backbone_instances"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="Explain shared transformer caching in simple terms.")
    parser.add_argument("--max-new-tokens", type=int, default=32)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    backend = build_orchestrator(Settings.from_env()).backend
    messages = [Message(role="user", content=args.prompt)]
    options = GenerationOptions(max_new_tokens=args.max_new_tokens)
    # Warm both transformer and LM paths before measuring.
    measure(backend, messages, options, False)
    rows = []
    for index in range(args.repeats):
        for naive in [True, False] if index % 2 == 0 else [False, True]:
            rows.append(measure(backend, messages, options, naive))
    for row in rows:
        expected = 2 if row["path"] == "naive" else 1
        assert row["prefill_calls"] == expected
        assert row["prompt_tokens_processed"] == row["prompt_tokens"] * expected
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
