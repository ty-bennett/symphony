import json
from types import SimpleNamespace

import huggingface_hub
import pytest

from scripts import test_qwen35_awq_jetson as smoke


def supported_platform():
    return {
        "machine": "aarch64",
        "board": "NVIDIA Jetson Orin Nano Developer Kit",
        "python": "3.12",
        "l4t": "# R39 (release), REVISION: 2.0, GCID: 123",
        "cuda": {"runtime_version": 13020, "compute_capability": "8.7"},
        "tensorrt": "10.13.3",
        "edge_llm": "0.11.0",
        "builder": "/venv/bin/builder",
        "ready": True,
        "blockers": [],
    }


@pytest.fixture
def checkpoint(tmp_path):
    directory = tmp_path / "checkpoint"
    directory.mkdir()
    config = {
        "model_type": "qwen3_5",
        "text_config": {"hidden_size": 2048, "num_hidden_layers": 24, "intermediate_size": 6144},
        "quantization_config": {
            "quant_method": "awq",
            "bits": 4,
            "group_size": 128,
            "version": "gemm",
            "zero_point": True,
            "modules_to_not_convert": ["visual", "linear_attn", "self_attn"],
        },
    }
    (directory / "config.json").write_text(json.dumps(config))
    (directory / "model.safetensors").write_bytes(b"synthetic-test-not-real-weights")
    (directory / "tokenizer.json").write_text("{}")
    (directory / "tokenizer_config.json").write_text("{}")
    return directory


def test_supported_orin_stack_has_no_blockers():
    assert smoke.platform_blockers(supported_platform()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("machine", "x86_64"),
        ("board", "Jetson Thor"),
        ("python", "3.14"),
        ("l4t", "# R36 (release), REVISION: 4.3"),
        ("tensorrt", "11.0"),
        ("edge_llm", "0.10.0"),
        ("builder", None),
    ],
)
def test_unsupported_stack_is_blocked(field, value):
    info = supported_platform()
    info[field] = value
    assert smoke.platform_blockers(info)


def test_doctor_has_no_model_or_download_side_effects(monkeypatch, capsys):
    monkeypatch.setattr(smoke.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(smoke, "read_text", lambda path: "")

    def forbidden(*args, **kwargs):
        pytest.fail("Doctor on an unsupported platform must not load native model/download/build")

    monkeypatch.setattr(smoke, "load_native", forbidden)
    monkeypatch.setattr(smoke, "download_checkpoint", forbidden)
    monkeypatch.setattr(smoke, "ensure_engine", forbidden)
    assert smoke.main(["doctor"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"


def test_failed_preflight_prevents_even_explicit_download(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(smoke, "doctor", lambda: {"ready": False, "blockers": ["test blocker"]})

    def forbidden(*args):
        pytest.fail("Blocked preflight must stop before any download")

    monkeypatch.setattr(smoke, "download_checkpoint", forbidden)
    assert smoke.main(["run", "--download", "--work-dir", str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["stage"] == "preflight"
    assert result["platform"]["blockers"] == ["test blocker"]


def test_download_is_pinned_safetensors_only_and_requires_no_router(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download", lambda *a, **kw: calls.append((a, kw))
    )
    smoke.download_checkpoint(tmp_path / "model")
    arguments, options = calls[0]
    assert arguments == ("QuantTrio/Qwen3.5-2B-AWQ",)
    assert len(options["revision"]) == 40
    assert options["revision"] == "b9b439ecbe34b097cfc9531132fedbf55fec9263"
    assert options["max_workers"] == 2
    assert not any(p.endswith((".py", ".bin", ".pt")) for p in options["allow_patterns"])


@pytest.mark.parametrize(
    "change",
    [
        {"quant_method": "gptq"},
        {"bits": 8},
        {"version": "marlin"},
        {"zero_point": False},
    ],
)
def test_wrong_quantization_is_rejected_not_fallen_back(checkpoint, change):
    config = json.loads((checkpoint / "config.json").read_text())
    config["quantization_config"].update(change)
    (checkpoint / "config.json").write_text(json.dumps(config))
    with pytest.raises(smoke.SmokeTestError, match="INT4 AWQ"):
        smoke.inspect_checkpoint(checkpoint)


def test_qwen3_not_35_is_rejected(checkpoint):
    config = json.loads((checkpoint / "config.json").read_text())
    config["model_type"] = "qwen3"
    (checkpoint / "config.json").write_text(json.dumps(config))
    with pytest.raises(smoke.SmokeTestError, match="Qwen3.5-2B"):
        smoke.inspect_checkpoint(checkpoint)


def test_default_work_directory_is_separate_from_old_4b_artifacts():
    args = smoke.parse_args(["run"])
    assert args.work_dir == smoke.Path("artifacts/qwen35-2b-awq").resolve()
    assert args.model_dir is None


def test_2b_checkpoint_architecture_is_accepted(checkpoint):
    metadata = smoke.inspect_checkpoint(checkpoint)
    assert metadata["quantization"]["bits"] == 4
    assert metadata["vision_engine_loaded"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"hidden_size": 2560},
        {"num_hidden_layers": 32},
        {"intermediate_size": 9216},
        {"hidden_size": 2560, "num_hidden_layers": 32, "intermediate_size": 9216},
    ],
)
def test_old_4b_architecture_is_rejected_before_build_or_load(checkpoint, change):
    config = json.loads((checkpoint / "config.json").read_text())
    config["text_config"].update(change)
    (checkpoint / "config.json").write_text(json.dumps(config))
    with pytest.raises(smoke.SmokeTestError, match="4B artifacts cannot be reused"):
        smoke.inspect_checkpoint(checkpoint)


def test_missing_weight_shard_is_rejected(checkpoint):
    (checkpoint / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "weight_map": {"mlp.weight": "missing-weights.safetensors"},
            }
        )
    )
    with pytest.raises(smoke.SmokeTestError, match="incomplete"):
        smoke.inspect_checkpoint(checkpoint)


def test_builder_is_text_only_batch_one_and_has_no_unquantized_export(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "/venv/bin/tensorrt-edgellm-build")
    args = smoke.parse_args(["run"])
    command = smoke.build_command(tmp_path / "model", tmp_path / "engine", args)
    assert command[command.index("--components") + 1] == "llm"
    assert command[command.index("--max-batch-size") + 1] == "1"
    assert command[command.index("--max-input-len") + 1] == "512"
    assert command[command.index("--max-kv-cache-capacity") + 1] == "1024"
    assert not any(x in command for x in ["--dtype", "visual", "export", "quantize"])


def test_engine_reuse_and_profile_change(monkeypatch, tmp_path, checkpoint):
    calls = []
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "builder")

    def build(command, **kwargs):
        calls.append(command)
        engine_dir = smoke.Path(command[command.index("--engine-dir") + 1])
        (engine_dir / "llm.engine").write_bytes(b"synthetic-engine")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke.subprocess, "run", build)
    args = smoke.parse_args(["run", "--work-dir", str(tmp_path)])
    info, metadata = supported_platform(), smoke.inspect_checkpoint(checkpoint)
    engine, _, reused = smoke.ensure_engine(checkpoint, args, info, metadata)
    assert not reused
    same, seconds, reused = smoke.ensure_engine(checkpoint, args, info, metadata)
    assert same == engine and seconds == 0 and reused
    assert len(calls) == 1
    args.max_input_len = 256
    changed, _, reused = smoke.ensure_engine(checkpoint, args, info, metadata)
    assert changed != engine and not reused and len(calls) == 2


def test_builder_failure_does_not_create_success_receipt(monkeypatch, tmp_path, checkpoint):
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "builder")
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=-9))
    args = smoke.parse_args(["run", "--work-dir", str(tmp_path)])
    with pytest.raises(smoke.SmokeTestError, match="exit -9"):
        smoke.ensure_engine(
            checkpoint, args, supported_platform(), smoke.inspect_checkpoint(checkpoint)
        )
    assert not list((tmp_path / "engines").rglob("smoke-test-manifest.json"))


def test_completed_retry_is_reused_and_failed_build_is_preserved(monkeypatch, tmp_path, checkpoint):
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "builder")
    calls = []

    def build(command, **kwargs):
        calls.append(command)
        engine = smoke.Path(command[command.index("--engine-dir") + 1])
        if len(calls) == 1:
            (engine / "failed-build-diagnostic.txt").write_text("synthetic build failure")
            return SimpleNamespace(returncode=1)
        (engine / "llm.engine").write_bytes(b"synthetic-engine")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke.subprocess, "run", build)
    args = smoke.parse_args(["run", "--work-dir", str(tmp_path)])
    info, metadata = supported_platform(), smoke.inspect_checkpoint(checkpoint)
    with pytest.raises(smoke.SmokeTestError, match="build failed"):
        smoke.ensure_engine(checkpoint, args, info, metadata)
    engine, _, reused = smoke.ensure_engine(checkpoint, args, info, metadata)
    assert not reused and "retry-" in engine.name
    same, _, reused = smoke.ensure_engine(checkpoint, args, info, metadata)
    assert same == engine and reused and len(calls) == 2
    assert len(list((tmp_path / "engines").rglob("failed-build-diagnostic.txt"))) == 1


class FakeNative:
    def __init__(self):
        self.loads = []
        self.requests = []

    def LLMRuntime(self, **kwargs):
        self.loads.append(kwargs)
        return self

    def create_text_message(self, role, content):
        return {"role": role, "content": content}

    def create_generation_request(self, messages, **kwargs):
        return SimpleNamespace(messages=messages, **kwargs)

    def handle_request(self, request):
        self.requests.append(request)
        return SimpleNamespace(output_texts=["SYNTHETIC_RESPONSE"], output_ids=[[1, 2, 3]])


def test_run_loads_one_backbone_and_report_contains_no_content(
    monkeypatch,
    tmp_path,
    checkpoint,
    capsys,
):
    native = FakeNative()
    monkeypatch.setattr(smoke, "doctor", supported_platform)
    monkeypatch.setattr(smoke, "load_native", lambda: native)
    monkeypatch.setattr(smoke, "ensure_engine", lambda *args: (tmp_path / "engine", 1.5, False))
    monkeypatch.setattr(
        smoke,
        "CudaProbe",
        lambda: SimpleNamespace(
            memory=lambda: {"free_bytes": 100, "total_bytes": 200},
        ),
    )
    report_path = tmp_path / "report.json"
    assert (
        smoke.main(
            [
                "run",
                "--model-dir",
                str(checkpoint),
                "--work-dir",
                str(tmp_path),
                "--prompt",
                "SYNTHETIC_PROMPT",
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text())
    assert report["status"] == "PASS"
    assert len(native.loads) == 1
    assert "multimodal_engine_dir" not in native.loads[0]
    assert len(native.requests) == 3  # One cold request + two warm requests, same loaded model.
    assert all(not r.enable_thinking and r.temperature == 0.0 for r in native.requests)
    assert report["cold_request"]["generated_tokens"] == 3
    assert report["cold_request"]["ttft_seconds"] is None
    assert report["memory"]["peak_cuda_allocation_bytes"] is None
    output = capsys.readouterr().out + report_path.read_text()
    assert "SYNTHETIC_PROMPT" not in output and "SYNTHETIC_RESPONSE" not in output


def test_default_run_downloads_and_loads_2b_in_its_own_work_directory(
    monkeypatch, tmp_path, checkpoint, capsys
):
    monkeypatch.chdir(tmp_path)
    native = FakeNative()
    monkeypatch.setattr(smoke, "doctor", supported_platform)
    monkeypatch.setattr(smoke, "load_native", lambda: native)
    expected_dir = tmp_path / "artifacts" / "qwen35-2b-awq" / "checkpoint"
    downloads = []

    def download(directory):
        downloads.append(directory)
        directory.mkdir(parents=True)
        for path in checkpoint.iterdir():
            smoke.shutil.copyfile(path, directory / path.name)

    monkeypatch.setattr(smoke, "download_checkpoint", download)
    monkeypatch.setattr(smoke, "ensure_engine", lambda *args: (tmp_path / "engine", 1.5, False))
    monkeypatch.setattr(
        smoke,
        "CudaProbe",
        lambda: SimpleNamespace(memory=lambda: {"free_bytes": 100, "total_bytes": 200}),
    )
    assert smoke.main(["run", "--download"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert downloads == [expected_dir]
    assert native.loads[0]["checkpoint_dir"] == str(expected_dir)
    assert report["model"] == {"id": smoke.MODEL_ID, "revision": smoke.MODEL_REVISION}
    assert report["status"] == "PASS"


def test_native_failure_does_not_echo_secret_exception_contents(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(smoke, "doctor", supported_platform)

    def fail(*args):
        raise RuntimeError("SYNTHETIC_SECRET_TOKEN")

    monkeypatch.setattr(smoke, "download_checkpoint", fail)
    assert smoke.main(["run", "--download", "--work-dir", str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert "SYNTHETIC_SECRET" not in output
    assert json.loads(output)["stage"] == "download"


def test_empty_generation_is_not_a_pass():
    native = FakeNative()
    native.handle_request = lambda request: SimpleNamespace(output_texts=[""], output_ids=[[]])
    with pytest.raises(smoke.SmokeTestError, match="no generated"):
        smoke.generate(native, native, "public", 16, False)


def test_limits_are_validated_before_runtime_use():
    with pytest.raises(SystemExit):
        smoke.parse_args(["run", "--max-kv-cache-capacity", "512"])
    with pytest.raises(SystemExit):
        smoke.parse_args(["run", "--runs", "0"])


def test_report_never_overwrites_an_existing_file(monkeypatch, tmp_path, capsys):
    path = tmp_path / "existing.json"
    path.write_text("user data")
    monkeypatch.setattr(
        smoke, "doctor", lambda: pytest.fail("Must fail before platform/model load")
    )
    assert smoke.main(["doctor", "--report", str(path)]) == 2
    assert path.read_text() == "user data"
    assert json.loads(capsys.readouterr().out)["error_type"] == "SmokeTestError"
