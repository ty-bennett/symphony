import hashlib
import json

import pytest
from conftest import fixed_heads

from edge_router.federation.checkpoint_manager import (
    DevelopmentModelPackageVerifier,
    Ed25519ModelPackageVerifier,
    FederatedCheckpointManager,
    load_checkpoint,
    save_checkpoint,
)
from edge_router.federation.model_gate import FederatedModelGate
from edge_router.federation.models import GateConfig


def lifecycle(backend, extractor, examples, policy, tmp_path, verifier=None, production=False):
    gate = FederatedModelGate(extractor, examples, policy, GateConfig())
    return FederatedCheckpointManager(
        backend.head_manager,
        backend.identity,
        verifier or DevelopmentModelPackageVerifier(),
        gate,
        tmp_path / "stage",
        production=production,
    )


@pytest.mark.parametrize(
    "key",
    ["base_model_id", "base_model_revision", "tokenizer_hash", "chat_template_hash", "hidden_size"],
)
def test_incompatible_package_keeps_current(backend, extractor, examples, policy, tmp_path, key):
    identity = dict(backend.identity)
    identity[key] = 24 if key == "hidden_size" else "incompatible"
    package = save_checkpoint(fixed_heads(backend, round=1), identity, tmp_path / "incoming")
    manager = lifecycle(backend, extractor, examples, policy, tmp_path)
    old = backend.head_manager.snapshot()
    assert not manager.activate(package)
    assert backend.head_manager.snapshot() is old
    assert manager.last_status == "PACKAGE_REJECTED"


def test_checkpoint_contains_no_backbone(backend, tmp_path):
    package = save_checkpoint(backend.head_manager.snapshot(), backend.identity, tmp_path / "heads")
    loaded = load_checkpoint(package.directory, backend.identity)
    assert set(loaded.heads.state_dict()) == set(backend.head_manager.snapshot().heads.state_dict())
    assert (package.directory / "heads.safetensors").stat().st_size < 20000


def test_failed_gate_keeps_previous(backend, extractor, examples, policy, tmp_path):
    package = save_checkpoint(
        fixed_heads(backend, sensitivity="PUBLIC", round=1), backend.identity, tmp_path / "bad"
    )
    manager = lifecycle(backend, extractor, examples, policy, tmp_path)
    old = backend.head_manager.snapshot()
    assert not manager.activate(package)
    assert backend.head_manager.snapshot() is old
    assert manager.last_status == "VALIDATION_REJECTED"


def test_production_rejects_development_verifier(backend, extractor, examples, policy, tmp_path):
    with pytest.raises(ValueError):
        lifecycle(backend, extractor, examples, policy, tmp_path, production=True)


def test_standard_signature_verification_and_tamper_detection(
    backend, extractor, examples, policy, tmp_path
):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = Ed25519PrivateKey.generate()
    verifier = Ed25519ModelPackageVerifier(
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    package = save_checkpoint(
        fixed_heads(backend, sensitivity="SENSITIVE", round=1),
        backend.identity,
        tmp_path / "signed",
    )
    manager = lifecycle(
        backend, extractor, examples, policy, tmp_path, verifier=verifier, production=True
    )
    assert not manager.activate(package)  # unsigned
    manifest = (package.directory / "manifest.json").read_bytes()
    (package.directory / "signature.bin").write_bytes(private.sign(manifest))
    assert manager.activate(package)
    assert manager.rollback()
    data = json.loads((package.directory / "config.json").read_text())
    data["global_round"] = 2
    (package.directory / "config.json").write_text(json.dumps(data))
    assert not manager.activate(package)  # artifacts changed under a valid old signature
    manifest = json.loads(manifest)
    manifest["config.json"] = hashlib.sha256(
        (package.directory / "config.json").read_bytes()
    ).hexdigest()
    (package.directory / "manifest.json").write_text(json.dumps(manifest))
    assert not manager.activate(package)  # unsigned replacement manifest
    assert backend.head_manager.snapshot().version == "tiny-bootstrap"
