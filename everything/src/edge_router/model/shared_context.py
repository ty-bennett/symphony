from dataclasses import dataclass, field
from typing import Any

import torch


@dataclass
class PrefillContext:
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    final_hidden_state: torch.Tensor
    past_key_values: Any
    prompt_length: int
    metadata: dict[str, Any] = field(default_factory=dict)
    consumed: bool = False


@dataclass
class Instrumentation:
    prefill_calls: int = 0
    prompt_tokens: int = 0
    decode_calls: int = 0
    decoded_input_tokens: int = 0
    backbone_instances: int = 1

    def snapshot(self) -> dict[str, int]:
        return vars(self).copy()


@dataclass(frozen=True)
class GeneratedToken:
    token_id: int
    text: str
    eos: bool = False
