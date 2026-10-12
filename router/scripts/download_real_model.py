"""Download the pinned public Gemma/Strands weights; never upload application data."""

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=project / "artifacts/hub")
    args = parser.parse_args()
    cache = args.cache_dir.resolve()
    # Keep both Hub weights and the transfer cache inside the project by default.
    os.environ["HF_HUB_CACHE"] = str(cache)
    os.environ["HF_XET_CACHE"] = str(cache.parent / "xet")
    from huggingface_hub import snapshot_download

    jobs = [
        (
            "StrandsAgents/strands-decider-E4B-gemma4-v1-2610",
            "1344d44397c457f3be34b8ff15c593b41248c6bf",
            [
                "strands_decider_config.json",
                "provenance.json",
                "head.safetensors",
                "lora/adapter_config.json",
                "lora/adapter_model.safetensors",
                "README.md",
                "LICENSE.md",
            ],
        ),
        (
            "google/gemma-4-E4B-it",
            "ee0ef6023621cff504d758262d4e04895a5af4a2",
            [
                "config.json",
                "generation_config.json",
                "model.safetensors",
                "tokenizer.json",
                "tokenizer_config.json",
                "chat_template.jinja",
                "README.md",
            ],
        ),
    ]
    print("Downloading about 16.5 GB. Existing downloads are cached/resumable.", flush=True)
    for repo, revision, files in jobs:
        print(json.dumps({"event": "download_started", "repo": repo}), flush=True)
        try:
            directory = snapshot_download(
                repo, revision=revision, allow_patterns=files, cache_dir=str(cache), max_workers=2
            )
        except Exception as error:
            response = getattr(error, "response", None)
            print(
                json.dumps(
                    {
                        "event": "download_failed",
                        "repo": repo,
                        "error_type": type(error).__name__,
                        "http_status": getattr(response, "status_code", None),
                    }
                ),
                flush=True,
            )
            return 1
        print(
            json.dumps({"event": "download_complete", "repo": repo, "directory": directory}),
            flush=True,
        )
    print("Original weights are ready. INT8 still requires local SmoothQuant calibration/export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
