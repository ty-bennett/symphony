"""Launch the preferred Qwen difficulty profile; one process, one backbone."""

import argparse
import os
from pathlib import Path

import uvicorn

from edge_router.testing.cli import read_profile


def main():
    project = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=project / ".env.qwen35-difficulty.example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    values = read_profile(args.profile)
    for name in [
        "QUANTIZED_MODEL_PATH",
        "DIFFICULTY_CHECKPOINT",
        "DIFFICULTY_POLICY_CONFIG",
        "POLICY_CONFIG",
        "STRANDS_PROMPT_CONFIG",
        "DECISION_CHECKPOINT",
    ]:
        if values.get(name) and not Path(values[name]).is_absolute():
            values[name] = str((project / values[name]).resolve())
    # Environment overrides are explicit operator configuration; profile supplies defaults.
    for name, value in values.items():
        os.environ.setdefault(name, value)
    uvicorn.run(
        "edge_router.api.app:create_app", factory=True, host=args.host, port=args.port, workers=1
    )


if __name__ == "__main__":
    main()
