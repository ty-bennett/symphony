"""Packed storage/layout verified against a small independent folded reference."""

import copy
import json

import pytest
import torch
from safetensors.torch import save_file
from test_qwen35_difficulty import small_config
from transformers import Qwen3_5ForCausalLM

from edge_router.model.memory import cache_bytes
from edge_router.model.quantization.modelopt_awq import (
    PackedAWQLinear,
    file_hash,
    load_packed_model,
    validate_export,
)


def pack(signed):
    # Independent implementation following ModelOpt pack_int4_in_uint8's transpose.
    transposed = signed.T.reshape(signed.shape[1], signed.shape[0] // 2, 2)
    return (
        ((transposed[..., 0] & 15) | ((transposed[..., 1] & 15) << 4))
        .T.to(torch.uint8)
        .contiguous()
    )


def test_signed_output_row_packing_scales_and_input_scaling():
    signed = torch.tensor(
        [[-8, -1, 0, 7], [1, 2, 3, 4], [7, 0, -1, -8], [-2, 3, -4, 5]], dtype=torch.int8
    )
    layer = PackedAWQLinear(4, 4, 2, 2, bias=True, device="cpu")
    layer.weight.copy_(pack(signed))
    layer.weight_scale.copy_(torch.tensor([[0.5, 1], [0.25, 0.5], [1, 0.25], [0.5, 0.25]]))
    layer.pre_quant_scale.copy_(torch.tensor([0.5, 2, 1, 0.25]))
    layer.bias.copy_(torch.arange(4))
    hidden = torch.tensor([[[1.0, 2.0, 3.0, 4.0]]])
    weight = signed.float() * layer.weight_scale.repeat_interleave(2, -1)
    expected = torch.nn.functional.linear(
        hidden * layer.pre_quant_scale, weight, layer.bias.float()
    )
    assert torch.equal(layer(hidden), expected)
    assert layer.weight.dtype == torch.uint8 and layer.weight.numel() == signed.numel() // 2
    assert not list(layer.parameters())
    with pytest.raises(RuntimeError, match="Backpropagation"):
        layer(hidden.requires_grad_())


@pytest.fixture
def export(tmp_path):
    config = small_config(128)
    config.tie_word_embeddings = True
    model = Qwen3_5ForCausalLM(config).half().eval().requires_grad_(False)
    directory = tmp_path / "edgellm_ckpt"
    directory.mkdir()
    excluded, state = ["lm_head", "model.embed_tokens"], {}
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear) and (
            module.out_features % 2 or module.in_features % 128
        ):
            excluded.append(name)
    original = copy.deepcopy(model)
    for name, value in model.state_dict().items():
        if name == "lm_head.weight":
            continue
        owner_name, field = name.rsplit(".", 1)
        module = model.get_submodule(owner_name)
        if isinstance(module, torch.nn.Linear) and owner_name not in excluded and field == "weight":
            groups = value.float().reshape(module.out_features, -1, 128)
            scale = groups.abs().amax(-1).clamp_min(1e-8) / 7
            quantized = (
                (groups / scale[..., None]).round().clamp(-8, 7).to(torch.int8).reshape_as(value)
            )
            state[name] = pack(quantized)
            state[owner_name + ".weight_scale"] = scale
            state[owner_name + ".pre_quant_scale"] = torch.ones(
                module.in_features, dtype=torch.float16
            )
            with torch.no_grad():
                original.get_submodule(owner_name).weight.copy_(
                    (quantized.reshape(module.out_features, -1, 128).float() * scale[..., None])
                    .reshape_as(value)
                    .half()
                )
        else:
            state[name] = value.clone()
    save_file(state, directory / "model.safetensors")
    data = config.to_dict()
    data["architectures"] = ["Qwen3_5ForCausalLM"]
    data["quantization_config"] = {"quant_method": "modelopt"}
    (directory / "config.json").write_text(json.dumps(data))
    quant = {
        "producer": {"name": "modelopt", "version": "0.45.0"},
        "quantization": {
            "quant_algo": "W4A16_AWQ",
            "group_size": 128,
            "has_zero_point": False,
            "pre_quant_scale": True,
            "kv_cache_quant_algo": None,
            "exclude_modules": excluded,
        },
    }
    (directory / "hf_quant_config.json").write_text(json.dumps(quant))
    model.generation_config.save_pretrained(directory)
    for name in ["tokenizer.json", "tokenizer_config.json", "chat_template.jinja"]:
        (directory / name).write_text("synthetic test asset")
    manifest = {
        "base_repo": "synthetic-qwen",
        "base_revision": "test",
        "chat_template_sha256": "test-template",
        "quantization": "int4_awq",
        "text_only": True,
        "versions": {"modelopt": "0.45.0"},
        "files": {"edgellm_ckpt/" + path.name: file_hash(path) for path in directory.iterdir()},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    identity = {
        "base_repo": "synthetic-qwen",
        "base_revision": "test",
        "chat_template_sha256": "test-template",
        "manifest_sha256": file_hash(manifest_path),
    }
    return tmp_path, directory, identity, original


def test_meta_loader_keeps_packed_weights_and_tied_lm_embedding(export):
    root, directory, identity, reference = export
    assert validate_export(root, identity) == directory
    model = load_packed_model(directory, "cpu", 32)
    assert model.lm_head.weight is model.model.embed_tokens.weight
    assert all(not parameter.requires_grad for parameter in model.parameters())
    assert not any(tensor.is_meta for tensor in list(model.parameters()) + list(model.buffers()))
    assert any(isinstance(module, PackedAWQLinear) for module in model.modules())
    ids = torch.tensor([[3, 5, 6, 7, 4, 5]])
    with torch.inference_mode():
        expected = reference(input_ids=ids, use_cache=True)
        actual = model(input_ids=ids, use_cache=True)
    torch.testing.assert_close(actual.logits, expected.logits, rtol=0.01, atol=0.001)
    assert cache_bytes(actual.past_key_values) > 0
    # Include GDN recurrent and convolution state, not just attention K/V tensors.
    assert cache_bytes(actual.past_key_values) > sum(
        layer.keys.numel() * layer.keys.element_size() * 2
        for layer in actual.past_key_values.layers
        if hasattr(layer, "keys")
    )


def test_export_integrity_mismatch_rejected(export):
    root, directory, identity, _ = export
    bad = {**identity, "manifest_sha256": "0" * 64}
    with pytest.raises(ValueError, match="manifest"):
        validate_export(root, bad)
    (directory / "tokenizer_config.json").write_text("modified")
    with pytest.raises(ValueError, match="hash"):
        validate_export(root, identity)


def test_unsupported_awq_recipe_rejected(export):
    _, directory, _, _ = export
    path = directory / "hf_quant_config.json"
    value = json.loads(path.read_text())
    value["quantization"]["has_zero_point"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Unsupported export"):
        load_packed_model(directory, "cpu", 32)
