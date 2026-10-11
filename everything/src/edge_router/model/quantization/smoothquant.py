"""SmoothQuant smoothing with real signed-INT8 storage and W8A8 linear GEMMs.

See Xiao et al., https://arxiv.org/abs/2211.10438. This implementation keeps
the inverse channel scale at the linear input instead of folding it into a
preceding norm. This preserves Gemma's RMSNorm/residual/PLE semantics, at the
cost of an unfused scaling operation. Attention BMMs are NOT quantized here.
No fake-quant floating-point weight matrix is retained or reconstructed.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F

ALIGNMENT = 32
MAX_REDUCTION = (2**31 - 1) // (127 * 127)


def padded_size(size: int) -> int:
    return math.ceil(size / ALIGNMENT) * ALIGNMENT


def check_int8_kernel(device: str | torch.device) -> None:
    """Probe the actual PyTorch build; unsupported hardware must fail explicitly.

    `_int_mm` is a private ATen entry point, isolated here for future replacement
    with a validated TensorRT/CUTLASS kernel. CI exercises its native CPU path;
    CUDA/JetPack availability must be verified on the deployment machine.
    """
    device = torch.device(device)
    if device.type not in {"cpu", "cuda"} or not hasattr(torch, "_int_mm"):
        raise RuntimeError("SmoothQuant requires a native PyTorch CPU/CUDA INT8 GEMM kernel")
    try:
        with torch.inference_mode():
            a = torch.ones((32, 32), dtype=torch.int8, device=device)
            product = torch._int_mm(a, a.t())
            if product.dtype != torch.int32 or not torch.all(product == 32).item():
                raise RuntimeError("INT8 kernel probe returned an invalid result")
    except (RuntimeError, NotImplementedError, AssertionError) as exc:
        raise RuntimeError(
            "Native INT8 GEMM unavailable; refusing floating-point fallback"
        ) from exc


def smoothing_scales(
    weight: torch.Tensor, activation_max: torch.Tensor, alpha: float
) -> torch.Tensor:
    """Channel smoothing s = amax(X)^alpha / amax(W)^(1-alpha)."""
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("SmoothQuant alpha must be finite and between zero and one")
    if weight.ndim != 2 or activation_max.shape != (weight.shape[1],):
        raise ValueError("Incompatible SmoothQuant calibration shape")
    activation_max = activation_max.to(device=weight.device, dtype=torch.float32)
    if (
        not torch.isfinite(weight).all()
        or not torch.isfinite(activation_max).all()
        or (activation_max < 0).any()
    ):
        raise ValueError("Non-finite or invalid SmoothQuant calibration")
    a = activation_max.clamp_min(1e-5)
    w = weight.float().abs().amax(dim=0).clamp_min(1e-5)
    return (a.pow(alpha) / w.pow(1 - alpha)).clamp_min(1e-5)


class SmoothQuantInt8Linear(nn.Module):
    """Per-output-channel INT8 weights, dynamically per-token INT8 activations.

    Only integer GEMM accumulates the matrix product (into INT32). FP32 scales
    dequantize the *output*, returned in the incoming residual-stream dtype.
    Padding handles small one-token decoding and native kernel alignment.
    """

    def __init__(self, in_features: int, out_features: int, bias: bool = False, device=None):
        super().__init__()
        if not 0 < in_features <= MAX_REDUCTION or out_features <= 0:
            raise ValueError("INT8 linear dimensions exceed safe INT32 accumulation bounds")
        self.in_features, self.out_features = in_features, out_features
        self.register_buffer(
            "weight_int8",
            torch.zeros(
                (padded_size(out_features), padded_size(in_features)),
                dtype=torch.int8,
                device=device,
            ),
        )
        self.register_buffer(
            "weight_scale", torch.ones(out_features, dtype=torch.float32, device=device)
        )
        self.register_buffer(
            "input_smoothing", torch.ones(in_features, dtype=torch.float32, device=device)
        )
        self.register_buffer(
            "bias", torch.zeros(out_features, dtype=torch.float32, device=device) if bias else None
        )

    @classmethod
    @torch.no_grad()
    def from_float(cls, linear: nn.Linear, activation_max: torch.Tensor, alpha: float = 0.5):
        if linear.bias is not None and not torch.isfinite(linear.bias).all():
            raise ValueError("Non-finite SmoothQuant bias")
        scales = smoothing_scales(linear.weight, activation_max, alpha)
        weight = linear.weight.float() * scales.unsqueeze(0)
        channel_scale = weight.abs().amax(dim=1).clamp_min(1e-8) / 127
        packed = (weight / channel_scale.unsqueeze(1)).round().clamp(-127, 127).to(torch.int8)
        result = cls(
            linear.in_features, linear.out_features, linear.bias is not None, linear.weight.device
        )
        result.weight_int8[: linear.out_features, : linear.in_features].copy_(packed)
        result.weight_scale.copy_(channel_scale)
        result.input_smoothing.copy_(scales)
        if linear.bias is not None:
            result.bias.copy_(linear.bias.float())
        return result.eval()

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        if not hidden.is_floating_point() or hidden.shape[-1] != self.in_features:
            raise ValueError("Invalid INT8 linear input")
        if hidden.numel() == 0:
            return hidden.new_empty((*hidden.shape[:-1], self.out_features))
        inputs = hidden.reshape(-1, self.in_features).float() / self.input_smoothing
        # Async checks avoid a CPU synchronization at every CUDA linear while
        # refusing to silently cast NaN/Inf to integers and fabricate predictions.
        torch._assert_async(torch.isfinite(inputs).all(), "Non-finite SmoothQuant input")
        token_scale = inputs.abs().amax(dim=1, keepdim=True).clamp_min(1e-8) / 127
        integer_inputs = (inputs / token_scale).round().clamp(-127, 127).to(torch.int8)
        rows = integer_inputs.shape[0]
        integer_inputs = F.pad(
            integer_inputs,
            (0, self.weight_int8.shape[1] - self.in_features, 0, padded_size(rows) - rows),
        ).contiguous()
        try:
            accum = torch._int_mm(integer_inputs, self.weight_int8.t())
        except (RuntimeError, NotImplementedError) as exc:
            raise RuntimeError(
                "SmoothQuant INT8 GEMM failed; no float fallback is permitted"
            ) from exc
        output = accum[:rows, : self.out_features].float() * token_scale * self.weight_scale
        if self.bias is not None:
            output = output + self.bias
        output = output.to(hidden.dtype).reshape(*hidden.shape[:-1], self.out_features)
        torch._assert_async(torch.isfinite(output).all(), "Non-finite SmoothQuant output")
        return output
