import pytest
import torch
from torch import nn

from edge_router.model.quantization.smoothquant import (
    SmoothQuantInt8Linear,
    check_int8_kernel,
    smoothing_scales,
)
from scripts.quantize_smoothquant import calibration_examples


def test_smoothing_preserves_linear_function_before_rounding():
    generator = torch.Generator().manual_seed(4)
    weights = torch.randn(32, 64, generator=generator)
    inputs = torch.randn(2, 64, generator=generator)
    inputs[:, 0] *= 200  # Synthetic activation outlier.
    scales = smoothing_scales(weights, inputs.abs().amax(0), 0.5)
    expected = inputs @ weights.t()
    actual = (inputs / scales) @ (weights * scales).t()
    torch.testing.assert_close(actual, expected, atol=1e-4, rtol=1e-5)
    assert scales[0] > scales[1:].median()


@pytest.mark.parametrize("shape", [(1, 17), (2, 3, 17), (32, 17)])
def test_packed_storage_and_integer_gemm_match_reference(shape, monkeypatch):
    torch.manual_seed(12)
    linear = nn.Linear(17, 13)
    inputs = torch.randn(shape)
    observed = inputs.reshape(-1, 17).abs().amax(0)
    quantized = SmoothQuantInt8Linear.from_float(linear, observed)
    calls = []
    original = torch._int_mm

    def record(left, right):
        calls.append((left.dtype, right.dtype))
        return original(left, right)

    monkeypatch.setattr(torch, "_int_mm", record)
    actual = quantized(inputs)
    # Independent integer arithmetic reference, not dequantized float GEMM.
    smooth = inputs.reshape(-1, 17) / quantized.input_smoothing
    scale = smooth.abs().amax(1, keepdim=True).clamp_min(1e-8) / 127
    integers = (smooth / scale).round().clamp(-127, 127).to(torch.int32)
    weight = quantized.weight_int8[:13, :17].to(torch.int32)
    expected = (integers @ weight.t()).float() * scale * quantized.weight_scale + linear.bias
    torch.testing.assert_close(actual, expected.reshape(*shape[:-1], 13))
    assert calls == [(torch.int8, torch.int8)]
    assert quantized.weight_int8.dtype == torch.int8
    assert not list(quantized.parameters())
    assert not hasattr(quantized, "weight")
    assert actual.shape == (*shape[:-1], 13)


def test_fp16_output_and_quantization_error_bound():
    torch.manual_seed(15)
    linear = nn.Linear(64, 64).half()
    inputs = torch.randn(8, 64).half()
    quantized = SmoothQuantInt8Linear.from_float(linear, inputs.float().abs().amax(0))
    actual = quantized(inputs)
    assert actual.dtype == torch.float16
    normalized_error = (actual.float() - linear(inputs).float()).norm() / linear(
        inputs
    ).float().norm()
    assert normalized_error < 0.03
    assert quantized.weight_int8.numel() + sum(
        v.numel() * v.element_size()
        for name, v in quantized.named_buffers()
        if name != "weight_int8"
    ) < sum(p.numel() * p.element_size() for p in linear.parameters())


def test_zero_inputs_and_weights_are_safe():
    linear = nn.Linear(32, 32, bias=False)
    linear.weight.data.zero_()
    quantized = SmoothQuantInt8Linear.from_float(linear, torch.zeros(32))
    assert torch.equal(quantized(torch.zeros(1, 32)), torch.zeros(1, 32))
    assert torch.isfinite(quantized.weight_scale).all()


def test_missing_kernel_never_falls_back_to_float(monkeypatch):
    linear = nn.Linear(32, 32)
    quantized = SmoothQuantInt8Linear.from_float(linear, torch.ones(32))

    def unavailable(*args):
        raise NotImplementedError("Synthetic unavailable native kernel")

    monkeypatch.setattr(torch, "_int_mm", unavailable)
    with pytest.raises(RuntimeError, match="refusing floating-point fallback"):
        check_int8_kernel("cpu")
    with pytest.raises(RuntimeError, match="no float fallback"):
        quantized(torch.zeros(1, 32))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_calibration_and_runtime_inputs_fail(value):
    linear = nn.Linear(32, 32)
    bad = torch.ones(32)
    bad[0] = value
    with pytest.raises(ValueError, match="Non-finite"):
        SmoothQuantInt8Linear.from_float(linear, bad)
    quantized = SmoothQuantInt8Linear.from_float(linear, torch.ones(32))
    with pytest.raises(RuntimeError, match="Non-finite SmoothQuant input"):
        quantized(bad.unsqueeze(0))


def test_calibration_errors_do_not_echo_local_content(tmp_path):
    path = tmp_path / "calibration.jsonl"
    path.write_text('{"messages":[{"role":"invalid","content":"SYNTHETIC_PRIVATE_TEXT"}]}')
    with pytest.raises(ValueError) as error:
        list(calibration_examples(path))
    assert "SYNTHETIC_PRIVATE_TEXT" not in str(error.value)
    assert "line 1" in str(error.value)
