import importlib.util
import json
from pathlib import Path

import pytest
import torch
from test_qwen35_difficulty import qwen as offline_qwen_fixture

from edge_router.model.decision_heads import HeadSnapshot
from edge_router.model.memory import component_memory
from edge_router.routing.difficulty_policy import DifficultyPolicyConfig, DifficultyRoutingPolicy
from edge_router.service.orchestrator import Orchestrator


@pytest.fixture
def qwen():
    return offline_qwen_fixture.__wrapped__()


@pytest.fixture
def script():
    path = Path(__file__).resolve().parents[1] / "scripts/test_qwen35_router_jetson.py"
    spec = importlib.util.spec_from_file_location("router_jetson_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_counts_packed_buffers_and_deduplicates_tied_head():
    backbone = torch.nn.Embedding(10, 4)
    backbone.register_buffer("packed", torch.zeros(20, dtype=torch.uint8))
    lm_head = torch.nn.Linear(4, 10, bias=False)
    lm_head.weight = backbone.weight
    difficulty = torch.nn.Linear(4, 1)
    report = component_memory(
        {"backbone": backbone, "lm_head": lm_head, "difficulty_head": difficulty}
    )
    assert report["components"]["backbone"]["resident_bytes"] == 180
    assert report["components"]["lm_head"]["resident_bytes"] == 160
    assert report["components"]["lm_head"]["additional_bytes"] == 0
    assert report["components"]["difficulty_head"]["additional_bytes"] == 20
    assert report["total_unique_bytes"] == 200


def test_local_smoke_generates_and_reuses_prefill(script, qwen):
    service = Orchestrator(qwen, DifficultyRoutingPolicy(DifficultyPolicyConfig()))
    report = script.run_test(service, max_new_tokens=3)
    assert report["status"] == "PASS"
    assert report["generated_tokens"] == 3 and report["shared_prefill_verified"]
    assert report["cache_after_prefill_bytes"] > 0
    assert qwen.instrumentation.prefill_calls == 1
    assert qwen.instrumentation.decoded_input_tokens == 2
    assert "difficulty_head" in report["component_memory"]["components"]


def test_cloud_skips_lm_unless_diagnostic_explicitly_enabled(script, qwen, monkeypatch):
    heads = qwen.head_manager.snapshot().heads.isolated_copy()
    with torch.no_grad():
        heads.network[-1].bias.fill_(1)
    qwen.head_manager.activate(HeadSnapshot(heads, "test-v2"))
    service = Orchestrator(qwen, DifficultyRoutingPolicy(DifficultyPolicyConfig()))
    with monkeypatch.context() as patch:
        patch.setattr(
            qwen.lm_head, "forward", lambda *args: pytest.fail("Cloud route cannot generate")
        )
        report = script.run_test(service, max_new_tokens=3)
    assert report["status"] == "PASS_ROUTING_ONLY" and report["generated_tokens"] == 0
    report = script.run_test(service, max_new_tokens=3, exercise_local=True)
    assert report["status"] == "PASS" and report["route"] == "PRIVATE_AWS"
    assert report["diagnostic_local_override"] and report["generated_tokens"] == 3
    assert report["shared_prefill_verified"]


def test_doctor_never_loads_real_model_or_downloads(script, monkeypatch, capsys):
    import edge_router.api.app as app

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(app, "build_orchestrator", lambda *args: pytest.fail("Cannot load model"))
    assert script.main(["--doctor", "--checkpoint", "/nonexistent/quant"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["ready"]
    assert any(item["code"] == "CUDA_UNAVAILABLE" for item in report["blockers"])


def test_no_cpu_fallback_and_safe_report_on_failure(script, tmp_path, capsys):
    path = tmp_path / "report.json"
    assert script.main(["--device", "cpu", "--output", str(path)]) == 1
    report = json.loads(path.read_text())
    assert report == {"status": "FAIL", "stage": "configuration", "error_code": "TEST_FAILED"}
    assert "messages" not in report and "response" not in report
    assert script.main(["--device", "cpu", "--output", str(path)]) == 2
    assert json.loads(path.read_text()) == report
    capsys.readouterr()
