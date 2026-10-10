import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

import torch
from safetensors.torch import load, save_file

from edge_router.federation.models import GlobalModelPackage, PackageMetadata
from edge_router.model.decision_heads import DecisionHeadManager, DecisionHeads, HeadSnapshot
from edge_router.observability.logging import log_event
from edge_router.schemas import Complexity, Sensitivity

ARTIFACTS = frozenset({"config.json", "heads.safetensors", "calibration.json"})
MAX_PACKAGE_BYTES = 128 * 1024 * 1024


class ModelPackageVerifier(Protocol):
    production_safe: bool

    def verify(self, manifest: bytes, signature: bytes | None) -> None: ...


class DevelopmentModelPackageVerifier:
    """Explicit development-only unsigned packages. Forbidden by production lifecycle."""

    production_safe = False

    def verify(self, manifest: bytes, signature: bytes | None) -> None:
        pass


class Ed25519ModelPackageVerifier:
    """Standard cryptography implementation; public key provisioned out of band."""

    production_safe = True

    def __init__(self, public_key_pem: bytes):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        self.public_key = load_pem_public_key(public_key_pem)
        if not isinstance(self.public_key, Ed25519PublicKey):
            raise ValueError("Expected an Ed25519 public key")

    def verify(self, manifest: bytes, signature: bytes | None) -> None:
        if signature is None:
            raise ValueError("Unsigned model package rejected")
        self.public_key.verify(signature, manifest)


def _read_bounded(path: Path) -> bytes:
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("Model package artifact exceeds size limit")
    with path.open("rb") as file:
        data = file.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("Model package artifact exceeds size limit")
    return data


def verify_compatibility(metadata: PackageMetadata, identity: dict):
    architecture = identity.get("head_architecture_version", DecisionHeads.architecture_version)
    if (
        metadata.federation_schema_version != 1
        or metadata.head_architecture_version != architecture
    ):
        raise ValueError("Unsupported head or federation schema")
    for key in [
        "base_model_id",
        "base_model_revision",
        "tokenizer_hash",
        "chat_template_hash",
        "hidden_size",
    ]:
        if getattr(metadata, key) != identity[key]:
            raise ValueError("Incompatible frozen-backbone identity")
    if metadata.complexity_label_schema != list(
        Complexity
    ) or metadata.sensitivity_label_schema != list(Sensitivity):
        raise ValueError("Incompatible label order")
    for key in ["backbone_adapter_hash", "decision_prompt_hash"]:
        if getattr(metadata, key) != identity.get(key):
            raise ValueError("Incompatible frozen adapter or decision rubric")


def read_package(package: GlobalModelPackage, identity: dict, verifier: ModelPackageVerifier):
    directory = package.directory
    manifest_bytes = _read_bounded(directory / "manifest.json")
    signature_path = directory / "signature.bin"
    signature = _read_bounded(signature_path) if signature_path.exists() else None
    verifier.verify(manifest_bytes, signature)
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or set(manifest) != ARTIFACTS:
        raise ValueError("Package must contain only approved decision-head artifacts")
    # Read once into bounded immutable buffers: verification/loading cannot race file changes.
    buffers = {name: _read_bounded(directory / name) for name in ARTIFACTS}
    if sum(map(len, buffers.values())) > MAX_PACKAGE_BYTES:
        raise ValueError("Model package too large")
    for name, data in buffers.items():
        if hashlib.sha256(data).hexdigest() != manifest[name]:
            raise ValueError("Package digest mismatch")
    metadata = PackageMetadata.model_validate_json(buffers["config.json"])
    verify_compatibility(metadata, identity)
    if metadata.head_architecture_version == DecisionHeads.architecture_version:
        factory = DecisionHeads
    else:
        from edge_router.model.strands_heads import StrandsDecisionHeads

        if metadata.head_architecture_version != StrandsDecisionHeads.architecture_version:
            raise ValueError("Unsupported decision-head architecture")
        factory = StrandsDecisionHeads
    heads = factory(metadata.hidden_size, metadata.head_width, metadata.head_dropout)
    weights = load(buffers["heads.safetensors"])
    expected = heads.state_dict()
    if set(weights) != set(expected) or any(
        value.shape != expected[name].shape
        or value.dtype != torch.float32
        or not torch.isfinite(value).all()
        for name, value in weights.items()
    ):
        raise ValueError("Invalid decision-head parameters")
    heads.load_state_dict(weights, strict=True)
    calibration = json.loads(buffers["calibration.json"])
    if set(calibration) != {"complexity_temperature", "sensitivity_temperature"}:
        raise ValueError("Invalid calibration schema")
    if not all(isinstance(t, (float, int)) and 0 < t < float("inf") for t in calibration.values()):
        raise ValueError("Invalid calibration temperature")
    snapshot = HeadSnapshot(
        heads, metadata.model_version, **calibration, global_round=metadata.global_round
    )
    return snapshot, buffers, manifest_bytes, signature


def load_checkpoint(directory: str | Path, identity: dict) -> HeadSnapshot:
    """Load an administrator-provisioned local checkpoint, not an incoming federation package."""
    return read_package(
        GlobalModelPackage(Path(directory)), identity, DevelopmentModelPackageVerifier()
    )[0]


def save_checkpoint(
    snapshot: HeadSnapshot, identity: dict, directory: str | Path
) -> GlobalModelPackage:
    destination = Path(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("Checkpoints are immutable; choose a new version directory")
    staged = Path(tempfile.mkdtemp(prefix="head-checkpoint-", dir=destination.parent))
    metadata = PackageMetadata.model_validate(
        {
            **identity,
            "model_version": snapshot.version,
            "global_round": snapshot.global_round,
            "head_architecture_version": snapshot.heads.architecture_version,
            "head_width": snapshot.heads.width,
            "head_dropout": snapshot.heads.dropout,
        }
    )
    (staged / "config.json").write_text(metadata.model_dump_json(indent=2))
    save_file(
        {k: v.detach().cpu().float().contiguous() for k, v in snapshot.heads.state_dict().items()},
        str(staged / "heads.safetensors"),
    )
    (staged / "calibration.json").write_text(
        json.dumps(
            {
                "complexity_temperature": snapshot.complexity_temperature,
                "sensitivity_temperature": snapshot.sensitivity_temperature,
            }
        )
    )
    manifest = {
        name: hashlib.sha256((staged / name).read_bytes()).hexdigest() for name in sorted(ARTIFACTS)
    }
    (staged / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    os.replace(staged, destination)
    return GlobalModelPackage(destination)


class FederatedCheckpointManager:
    def __init__(
        self,
        heads: DecisionHeadManager,
        identity: dict,
        verifier: ModelPackageVerifier,
        gate,
        staging_directory: str | Path,
        production: bool = True,
    ):
        if production and not verifier.production_safe:
            raise ValueError("Production activation requires a signature verifier")
        if production and identity["base_model_revision"] in {"main", "master", "latest", ""}:
            raise ValueError("Production federation requires a pinned backbone revision")
        self.heads, self.identity, self.verifier, self.gate = heads, identity, verifier, gate
        self.directory = Path(staging_directory)
        self.production = production
        self._lock = threading.RLock()
        self.last_status = "NOT_ATTEMPTED"
        self._highest_round = heads.snapshot().global_round
        self._active_stage: Path | None = None
        self._previous_stage: Path | None = None
        pointer = self.directory / "active.json"
        if pointer.exists():
            data = json.loads(_read_bounded(pointer))
            self._highest_round = max(self._highest_round, int(data["highest_round"]))

    def _publish_pointer(self, stage: Path | None, highest_round: int):
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as file:
            json.dump(
                {"directory": stage.name if stage else None, "highest_round": highest_round}, file
            )
            file.flush()
            os.fsync(file.fileno())
            temporary = file.name
        os.replace(temporary, self.directory / "active.json")

    def restore_active(self) -> bool:
        """Reverify and revalidate the durable pointer after a process restart."""
        with self._lock:
            try:
                data = json.loads(_read_bounded(self.directory / "active.json"))
                name = data["directory"]
                if not name or Path(name).name != name:
                    return False
                stage = self.directory / name
                candidate = read_package(GlobalModelPackage(stage), self.identity, self.verifier)[0]
                candidate.heads.to(next(self.heads.snapshot().heads.parameters()).device)
                if not self.gate.evaluate(candidate).accepted:
                    return False
                self.heads.activate(candidate)
                self._active_stage = stage
                self._highest_round = max(self._highest_round, candidate.global_round)
                return True
            except Exception:
                log_event("federated_restore_rejected", status="PACKAGE_REJECTED")
                return False

    def activate(self, package: GlobalModelPackage) -> bool:
        with self._lock:
            try:
                candidate, buffers, manifest, signature = read_package(
                    package, self.identity, self.verifier
                )
                if candidate.global_round <= self._highest_round:
                    raise ValueError("Stale federation round")
                device = next(self.heads.snapshot().heads.parameters()).device
                candidate.heads.to(device)
                result = self.gate.evaluate(candidate)
                if not result.accepted:
                    self.last_status = "VALIDATION_REJECTED"
                    log_event("federated_candidate_rejected", status=self.last_status)
                    return False
                self.directory.mkdir(parents=True, exist_ok=True)
                stage = Path(
                    tempfile.mkdtemp(prefix=f"round-{candidate.global_round}-", dir=self.directory)
                )
                for name, data in buffers.items():
                    (stage / name).write_bytes(data)
                (stage / "manifest.json").write_bytes(manifest)
                if signature is not None:
                    (stage / "signature.bin").write_bytes(signature)
                # Publication is an atomic pointer swap; backbone modules are untouched.
                self.heads.activate(candidate)
                try:
                    self._publish_pointer(stage, candidate.global_round)
                except Exception:
                    self.heads.rollback()
                    raise
                self._previous_stage, self._active_stage = self._active_stage, stage
                self._highest_round = candidate.global_round
                self.last_status = "ACTIVATED"
                log_event(
                    "federated_model_activated",
                    version=candidate.version,
                    global_round=candidate.global_round,
                )
                return True
            except Exception:
                self.last_status = "PACKAGE_REJECTED"
                log_event("federated_candidate_rejected", status=self.last_status)
                return False

    def rollback(self) -> bool:
        with self._lock:
            if not self.heads.rollback():
                return False
            try:
                self._publish_pointer(self._previous_stage, self._highest_round)
            except Exception:
                self.heads.rollback()
                return False
            self._active_stage, self._previous_stage = self._previous_stage, self._active_stage
            return True
