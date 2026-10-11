import json
from pathlib import Path

import pytest
from conftest import fixed_heads

from edge_router.config import Settings
from edge_router.model.memory import parameter_hash
from edge_router.schemas import GenerationOptions, Message
from edge_router.testing.cli import main, read_cases, read_profile, save_report
from edge_router.testing.harness import EvaluationCase, ModelTestHarness, summarize
from edge_router.testing.readiness import readiness

PROJECT = Path(__file__).resolve().parents[1]


def test_harness_chat_measures_ttft_and_single_prefill(service, backend):
    before = (
        parameter_hash(backend.backbone),
        parameter_hash(backend.lm_head),
        parameter_hash(backend.head_manager.snapshot().heads),
    )
    events = []
    outcome = ModelTestHarness(service).run(
        EvaluationCase(messages=[Message(role="user", content="hello")]),
        GenerationOptions(max_new_tokens=3),
        on_event=events.append,
    )
    assert outcome.shared_prefill_verified
    assert outcome.instrumentation["prefill_calls"] == 1
    assert outcome.generated_tokens == 3
    assert outcome.decision.timing_ms["time_to_first_token"] > 0
    assert outcome.safe_record()["decode_tokens_per_second"] > 0
    assert before == (
        parameter_hash(backend.backbone),
        parameter_hash(backend.lm_head),
        parameter_hash(backend.head_manager.snapshot().heads),
    )
    assert events[0].event == "decision" and events[-1].event == "complete"


def test_decision_only_never_uses_lm_head(service, backend):
    def forbidden(*args):
        raise AssertionError("No local generation expected")

    backend.lm_head.forward = forbidden
    result = ModelTestHarness(service).run(
        EvaluationCase(messages=[Message(role="user", content="hello")]),
        GenerationOptions(max_new_tokens=3),
        mode="decision",
    )
    assert result.status == "DECISION_ONLY"
    assert result.generated_tokens == 0
    assert result.instrumentation["decoded_input_tokens"] == 0
    assert result.safe_record()["time_to_first_token_ms"] is None


def test_remote_route_is_handoff_not_forced_local(service, backend):
    backend.head_manager.activate(fixed_heads(backend, complexity="HIGH", sensitivity="PUBLIC"))
    result = ModelTestHarness(service).run(
        EvaluationCase(messages=[Message(role="user", content="public synthetic request")]),
        GenerationOptions(max_new_tokens=3),
    )
    assert result.status == "HANDOFF_REQUIRED"
    assert result.decision.route == "FRONTIER"
    assert result.generated_tokens == 0
    assert result.shared_prefill_verified


def test_ground_truth_labels_do_not_override_runtime_policy(service, backend):
    backend.head_manager.activate(fixed_heads(backend, complexity="HIGH", sensitivity="PUBLIC"))
    case = EvaluationCase(
        messages=[
            Message(
                role="user", content="synthetic context without a deterministic sensitive marker"
            )
        ],
        complexity="HIGH",
        sensitivity="SENSITIVE",
        route="PRIVATE_AWS",
    )
    outcome = ModelTestHarness(service).run(
        case, GenerationOptions(max_new_tokens=3), mode="decision"
    )
    report = summarize([case], [outcome], backend.model_info())
    assert outcome.decision.route == "FRONTIER"
    assert report["sensitivity"]["sensitive_false_negative_rate"] == 1
    assert report["routing"]["ground_truth_sensitive_examples_allowed_frontier"] == 1
    assert report["routing"]["deterministic_invariant_violations"] == 0
    assert report["quality_test_valid"] is False


def test_trusted_security_metadata_still_blocks_frontier(service, backend):
    backend.head_manager.activate(fixed_heads(backend, complexity="HIGH", sensitivity="PUBLIC"))
    case = EvaluationCase(
        messages=[Message(role="user", content="synthetic confidential example")],
        security_context={"classification": "SENSITIVE"},
    )
    result = ModelTestHarness(service).run(case, GenerationOptions(max_new_tokens=3))
    assert result.decision.route == "PRIVATE_AWS"
    assert result.generated_tokens == 0


def test_reports_and_logs_never_persist_content(service, backend, tmp_path, caplog):
    case = EvaluationCase(
        messages=[Message(role="user", content="password=SYNTHETIC_CANARY_NEVER_SAVE")]
    )
    outcome = ModelTestHarness(service).run(case, GenerationOptions(max_new_tokens=3))
    report = summarize([case], [outcome], backend.model_info())
    path = tmp_path / "metrics.json"
    save_report(path, report)
    assert "SYNTHETIC_CANARY_NEVER_SAVE" not in path.read_text()
    assert "SYNTHETIC_CANARY_NEVER_SAVE" not in caplog.text
    assert not {
        "messages",
        "text",
        "response",
        "hidden_states",
        "input_ids",
        "matches",
    }.intersection(report["results"][0])
    with pytest.raises(FileExistsError):
        save_report(path, report)


def test_malformed_case_and_shell_profile_are_not_echoed_or_executed(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"messages":[{"role":"invalid","content":"SYNTHETIC_CANARY_NEVER_ECHO"}]}')
    with pytest.raises(ValueError) as error:
        read_cases(path)
    assert "SYNTHETIC_CANARY" not in str(error.value)
    marker = tmp_path / "must-not-exist"
    profile = tmp_path / "bad.env"
    profile.write_text(f'MODEL_ID="$(touch {marker})"\n')
    with pytest.raises(ValueError):
        read_profile(profile)
    assert not marker.exists()


def test_doctor_reports_missing_gpu_and_artifact_without_loading_model(monkeypatch, capsys):
    import torch

    import edge_router.api.app as app

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        app, "build_orchestrator", lambda *args: pytest.fail("Doctor must not load a model")
    )
    code = main(
        [
            "--doctor",
            "--profile",
            str(PROJECT.parent / ".env.gemma4-strands.example"),
            "--checkpoint",
            "/nonexistent/approved-int8-artifact",
        ],
        PROJECT,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert {item["code"] for item in report["blockers"]} == {
        "CUDA_UNAVAILABLE",
        "QUANTIZED_CHECKPOINT_MISSING",
    }


def test_demo_cli_runs_from_any_cwd_and_does_not_claim_real_model_quality(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "metrics.json"
    code = main(
        [
            "--demo",
            "--prompt",
            "synthetic sample",
            "--max-new-tokens",
            "2",
            "--output",
            str(output),
        ],
        PROJECT,
    )
    assert code == 0
    report = json.loads(output.read_text())
    assert report["quality_test_valid"] is False
    assert report["performance"]["shared_prefill_verified_requests"] == 1
    assert "synthetic sample" not in output.read_text()
    assert "OFFLINE DEMO" in capsys.readouterr().out


def test_demo_batch_covers_cases_and_has_classification_metrics(tmp_path):
    output = tmp_path / "batch.json"
    code = main(
        [
            "--demo",
            "--cases",
            str(PROJECT / "examples/test_cases.jsonl"),
            "--mode",
            "decision",
            "--warmup",
            "1",
            "--output",
            str(output),
        ],
        PROJECT,
    )
    assert code == 0
    report = json.loads(output.read_text())
    assert report["requests"] == 6
    assert report["complexity"]["examples"] == 6
    assert report["sensitivity"]["examples"] == 6
    assert report["performance"]["shared_prefill_verified_requests"] == 6
    assert all(row["generated_tokens"] == 0 for row in report["results"])


def test_readiness_demo_is_not_a_real_quality_test():
    result = readiness(Settings(model_backend="tiny", device="cpu"))
    assert result["ready"]
    assert "DEMO" in result["warnings"][0]
