import argparse
import json
import resource
import tempfile
from pathlib import Path

import torch

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings, read_yaml
from edge_router.federation.checkpoint_manager import save_checkpoint
from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.models import FeatureMode, FederationConfig
from edge_router.federation.update_builder import save_update
from edge_router.model.memory import parameter_hash, synchronize
from training.dataset import JsonlDataset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--training-config", default="config/federation.yaml")
    args = parser.parse_args()
    service = build_orchestrator(Settings.from_env())
    backend = service.backend
    extractor = FrozenFeatureExtractor(backend)
    config = FederationConfig.model_validate(read_yaml(args.training_config))
    examples = JsonlDataset(args.data).examples()[: config.max_examples_per_round]
    before, lm_before = parameter_hash(backend.backbone), parameter_hash(backend.lm_head)
    rows = []
    output = Path(tempfile.mkdtemp(prefix="head-training-benchmark-"))
    for label, mode in [
        ("ONLINE_FEATURE_EXTRACTION", FeatureMode.ONLINE_FEATURE_EXTRACTION),
        ("PRECOMPUTED_FEATURES", FeatureMode.PRECOMPUTED_FEATURES),
        ("PRECOMPUTED_FEATURES_REUSED", FeatureMode.PRECOMPUTED_FEATURES),
    ]:
        features = (
            [extractor.extract(example) for example in examples]
            if label.endswith("REUSED")
            else None
        )
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        synchronize()
        local_config = config.model_copy(update={"feature_mode": mode})
        prefill_before = backend.instrumentation.prefill_calls
        result = FederatedLocalTrainer(extractor, local_config).train(
            backend.head_manager.snapshot(), examples if features is None else None, features
        )
        synchronize()
        directory = output / label
        save_checkpoint(result.candidate, backend.identity, directory / "checkpoint")
        save_update(result.update, directory / "update")
        after, lm_after = parameter_hash(backend.backbone), parameter_hash(backend.lm_head)
        rows.append(
            {
                "mode": label,
                "examples": len(examples),
                "feature_extraction_seconds": result.feature_extraction_seconds,
                "head_training_seconds": result.head_training_seconds,
                "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated()
                if torch.cuda.is_available()
                else None,
                "peak_cpu_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "checkpoint_bytes": sum(
                    p.stat().st_size for p in (directory / "checkpoint").iterdir()
                ),
                "update_bytes": sum(p.stat().st_size for p in (directory / "update").iterdir()),
                "backbone_parameter_changes": 0 if before == after else "FAILED",
                "lm_head_parameter_changes": 0 if lm_before == lm_after else "FAILED",
                "prefill_calls_during_training": backend.instrumentation.prefill_calls
                - prefill_before,
            }
        )
        assert before == after and lm_before == lm_after
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
