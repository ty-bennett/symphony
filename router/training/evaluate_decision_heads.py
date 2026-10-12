import argparse
import json

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings
from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from training.dataset import JsonlDataset
from training.evaluation import evaluate_heads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    service = build_orchestrator(Settings.from_env())
    report = evaluate_heads(
        service.backend.head_manager.snapshot(),
        FrozenFeatureExtractor(service.backend),
        JsonlDataset(args.data).examples(),
        service.policy,
    )
    print(json.dumps(report, indent=2))
    if report["routing"]["sensitive_examples_allowed_frontier"]:
        raise SystemExit("Evaluation failed: sensitive examples reached FRONTIER")


if __name__ == "__main__":
    main()
