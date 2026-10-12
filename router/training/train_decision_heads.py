import argparse
import json

import torch

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings, read_yaml
from edge_router.federation.checkpoint_manager import save_checkpoint
from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FederationConfig
from edge_router.model.decision_heads import HeadSnapshot
from training.dataset import JsonlDataset
from training.evaluation import calibrate_temperature, evaluate_heads


def main():
    parser = argparse.ArgumentParser(
        description="Train only decision heads on approved local JSONL"
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--training-config", default="config/federation.yaml")
    parser.add_argument("--validation-data")
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()
    service = build_orchestrator(Settings.from_env())
    extractor = FrozenFeatureExtractor(service.backend)
    config = FederationConfig.model_validate(read_yaml(args.training_config))
    result = FederatedLocalTrainer(extractor, config).train(
        service.backend.head_manager.snapshot(), JsonlDataset(args.data).examples()
    )
    ct, st = result.candidate.complexity_temperature, result.candidate.sensitivity_temperature
    validation = JsonlDataset(args.validation_data).examples() if args.validation_data else None
    if args.calibrate:
        if not validation:
            parser.error("--calibrate requires held-out --validation-data")
        features = [extractor.extract(x) for x in validation]
        device = next(result.candidate.heads.parameters()).device
        with torch.no_grad():
            c, s = result.candidate.heads(torch.stack([f.vector for f in features]).to(device))
        for head, logits, labels in [
            ("complexity", c, [f.complexity_index for f in features]),
            ("sensitivity", s, [f.sensitivity_index for f in features]),
        ]:
            target = torch.tensor(labels, device=device)
            mask = target >= 0
            if not mask.any():
                raise ValueError("Calibration needs eligible held-out labels for both heads")
            temperature = calibrate_temperature(logits[mask], target[mask])
            if head == "complexity":
                ct = temperature
            else:
                st = temperature
    snapshot = HeadSnapshot(result.candidate.heads, "head-only-trained-v1", ct, st)
    save_checkpoint(snapshot, service.backend.identity, args.output)
    report = {"training": result.update.aggregate_training_metrics, "checkpoint": args.output}
    if validation:
        report["validation"] = evaluate_heads(snapshot, extractor, validation, service.policy)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
