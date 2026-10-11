import argparse
import json
import tempfile
from pathlib import Path

from edge_router.federation.checkpoint_manager import (
    DevelopmentModelPackageVerifier,
    FederatedCheckpointManager,
    save_checkpoint,
)
from edge_router.federation.client import FederatedClient
from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.federation.local_trainer import FederatedLocalTrainer
from edge_router.federation.model_gate import FederatedModelGate
from edge_router.federation.models import FederationConfig, GateConfig
from edge_router.federation.simulation_adapter import LocalSimulationFederationAdapter
from edge_router.feedback.models import LocalExample
from edge_router.model.tiny_backend import TinyBackend
from edge_router.routing.policy import PolicyConfig, RoutingPolicy
from edge_router.schemas import Message


def examples(complexity, sensitivity, count=4):
    return [
        LocalExample(
            messages=[Message(role="user", content=f"Synthetic example {i}")],
            complexity=complexity,
            sensitivity=sensitivity,
        )
        for i in range(count)
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    output = Path(args.output or tempfile.mkdtemp(prefix="edge-federation-demo-"))
    backend = TinyBackend()
    baseline = backend.head_manager.snapshot()
    package = save_checkpoint(baseline, backend.identity, output / "global-0")
    adapter = LocalSimulationFederationAdapter(package, baseline, backend.identity)
    policy = RoutingPolicy(PolicyConfig.load("config/routing_policy.yaml"))
    protected = examples("LOW", "PUBLIC") + examples("LOW", "SENSITIVE")
    statuses = []
    for name, complexity, sensitivity in [
        ("A", "LOW", "PUBLIC"),
        ("B", "HIGH", "PUBLIC"),
        ("C", "HIGH", "SENSITIVE"),
    ]:
        local = TinyBackend()
        extractor = FrozenFeatureExtractor(local)
        config = FederationConfig(enabled=True, secure_aggregation_required=False)
        gate = FederatedModelGate(extractor, protected, policy, GateConfig())
        checkpoints = FederatedCheckpointManager(
            local.head_manager,
            local.identity,
            DevelopmentModelPackageVerifier(),
            gate,
            output / f"client-{name}",
            production=False,
        )
        client = FederatedClient(
            adapter, FederatedLocalTrainer(extractor, config), checkpoints, config
        )
        submitted = client.run_round(examples(complexity, sensitivity))
        statuses.append({"client": name, "submitted": submitted, "status": client.status})
    package = adapter.aggregate(output / "global-1")
    gate = FederatedModelGate(FrozenFeatureExtractor(backend), protected, policy, GateConfig())
    lifecycle = FederatedCheckpointManager(
        backend.head_manager,
        backend.identity,
        DevelopmentModelPackageVerifier(),
        gate,
        output / "activated",
        production=False,
    )
    activated = lifecycle.activate(package)
    print(
        json.dumps(
            {
                "development_only": True,
                "secure_aggregation": False,
                "clients": statuses,
                "activated": activated,
                "active_version": backend.head_manager.snapshot().version,
                "output": str(output),
            },
            indent=2,
        )
    )
    if not all(s["submitted"] for s in statuses) or not activated:
        raise SystemExit("Simulation failed")


if __name__ == "__main__":
    main()
