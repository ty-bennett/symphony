from pathlib import Path

import pytest

from edge_router.config import Settings
from edge_router.model.qwen35_difficulty_backend import build_qwen35_difficulty_backend
from edge_router.testing.cli import main
from edge_router.testing.readiness import readiness

PROJECT = Path(__file__).resolve().parents[1]


def test_preferred_doctor_reports_head_and_missing_exact_backbone(monkeypatch, capsys):
    import json

    import torch

    import edge_router.api.app as app

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        app, "build_orchestrator", lambda *args: pytest.fail("Doctor cannot load backbone")
    )
    assert main(["--doctor", "--checkpoint", "/nonexistent/qwen-quant"], PROJECT) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["backend"] == "qwen35_difficulty"
    assert report["decision_model_version"] == "331d32a75ad0a62a"
    assert report["decision_layer"] == 10
    assert {block["code"] for block in report["blockers"]} == {
        "CUDA_UNAVAILABLE",
        "QUANTIZED_CHECKPOINT_MISSING_OR_INCOMPATIBLE",
    }


def test_unquantized_or_unrelated_awq_cannot_silently_substitute():
    with pytest.raises(ValueError, match="exact ModelOpt"):
        build_qwen35_difficulty_backend(Settings(model_backend="qwen35_difficulty"))
    settings = Settings(
        model_backend="qwen35_difficulty",
        model_id="Qwen/Qwen3.5-2B",
        model_revision="wrong-revision",
        dtype="float16",
        quantization="modelopt_awq_int4",
        quantized_model_path="/nonexistent/quant",
        difficulty_checkpoint=str(PROJECT.parent / "router_heads/out/heads/difficulty.pt"),
        difficulty_policy_config=str(PROJECT / "config/difficulty_policy.yaml"),
    )
    result = readiness(settings)
    assert not result["ready"]
    assert result["blockers"][0]["code"] == "DIFFICULTY_HEAD_CONFIG_MISMATCH"
    with pytest.raises(ValueError, match="revision"):
        build_qwen35_difficulty_backend(settings)
