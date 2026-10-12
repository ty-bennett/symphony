import json
from pathlib import Path

import huggingface_hub

from scripts import download_real_model, quantize_smoothquant

PROJECT = Path(__file__).resolve().parents[1]


def test_download_uses_pinned_revisions_and_only_model_artifacts(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "old-hub"))
    monkeypatch.setenv("HF_XET_CACHE", str(tmp_path / "old-xet"))
    monkeypatch.setattr(
        "sys.argv", ["download_real_model.py", "--cache-dir", str(tmp_path / "hub")]
    )

    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        return str(tmp_path / "snapshot")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert download_real_model.main() == 0
    assert len(calls) == 2
    assert all(len(options["revision"]) == 40 for _, options in calls)
    assert all(options["cache_dir"] == str(tmp_path / "hub") for _, options in calls)
    assert "model.safetensors" in calls[1][1]["allow_patterns"]
    assert "lora/adapter_model.safetensors" in calls[0][1]["allow_patterns"]
    assert not any(
        name.endswith((".jsonl", ".pt", ".bin", ".py"))
        for _, options in calls
        for name in options["allow_patterns"]
    )
    assert "INT8 still requires" in capsys.readouterr().out


def test_download_failure_does_not_expose_exception_contents(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "old-hub"))
    monkeypatch.setenv("HF_XET_CACHE", str(tmp_path / "old-xet"))
    monkeypatch.setattr(
        "sys.argv", ["download_real_model.py", "--cache-dir", str(tmp_path / "hub")]
    )

    def forbidden(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_BE_LOGGED")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", forbidden)
    assert download_real_model.main() == 1
    output = capsys.readouterr().out
    assert "download_failed" in output
    assert "SYNTHETIC_SECRET" not in output


def test_converter_profile_works_from_root_without_network(monkeypatch, tmp_path, capsys):
    import edge_router.model.gemma4_strands_backend as gemma
    import edge_router.model.quantization.checkpoint as checkpoint
    import edge_router.model.quantization.gemma4 as quantizer

    seen = {}

    def build(settings):
        seen["settings"] = settings
        return object()

    def quantize(backend, examples, alpha, maximum):
        seen["examples"] = len(list(examples))
        return {"method": "smoothquant_int8", "linear_modules": {}}

    def save(backend, recipe, path):
        path.mkdir()
        return path

    monkeypatch.setattr(gemma, "build_gemma4_strands_backend", build)
    monkeypatch.setattr(quantizer, "quantize_backend", quantize)
    monkeypatch.setattr(checkpoint, "save_quantized_backend", save)
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    monkeypatch.chdir(PROJECT.parent)
    monkeypatch.setattr(
        "sys.argv",
        [
            "quantize_smoothquant.py",
            "--profile",
            str(PROJECT / ".env.gemma4-strands.cpu.example"),
            "--calibration-jsonl",
            str(PROJECT / "examples/test_cases.jsonl"),
            "--output",
            str(tmp_path / "int8"),
            "--max-examples",
            "6",
        ],
    )
    quantize_smoothquant.main()
    settings = seen["settings"]
    assert settings.device == "cpu"
    assert settings.quantization == "none"
    assert settings.local_files_only is True
    assert len(settings.strands_revision) == 40
    assert Path(settings.strands_prompt_config).is_file()
    assert seen["examples"] == 6
    assert json.loads(capsys.readouterr().out)["method"] == "smoothquant_int8"
