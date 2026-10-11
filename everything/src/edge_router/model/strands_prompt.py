"""Append two typed questions inside the final user turn, before assistant startup.

Offsets are obtained from one tokenization of the COMPLETE native chat prompt.
Never tokenize/concatenate fragments independently: BPE boundary merges would
otherwise shift the readout positions. No new tokenizer tokens are introduced.
"""

from dataclasses import dataclass

import torch
from pydantic import Field

from edge_router.config import read_yaml
from edge_router.schemas import Complexity, Message, Sensitivity, StrictModel


class Requirement(StrictModel):
    instructions: str = Field(min_length=1)
    criteria: dict[str, str]


class StrandsPromptConfig(StrictModel):
    complexity: Requirement
    sensitivity: Requirement
    generation_instruction: str = Field(min_length=1)

    @classmethod
    def load(cls, path: str) -> "StrandsPromptConfig":
        result = cls.model_validate(read_yaml(path))
        if list(result.complexity.criteria) != list(Complexity) or list(
            result.sensitivity.criteria
        ) != list(Sensitivity):
            raise ValueError("Decision rubric labels/order do not match the API schemas")
        return result


@dataclass(frozen=True)
class EncodedStrandsPrompt:
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    feature_positions: tuple[int, ...]


def _question(requirement: Requirement) -> tuple[str, list[tuple[int, int]]]:
    text = (
        '<question type="choice">\nSelect exactly one option.\n'
        + requirement.instructions.strip()
        + "\n<options>\n"
    )
    option_spans = []
    for i, (label, description) in enumerate(requirement.criteria.items(), 1):
        line = f"{i}. {label} — {' '.join(description.split())}"
        option_spans.append((len(text), len(text) + len(line)))
        text += line + "\n"
    text += "</options>\n</question>\n"
    answer_span = (len(text), len(text) + len("<answer>"))
    text += "<answer>"
    return text, [answer_span, *option_spans]


def encode_strands_prompt(
    tokenizer, messages: list[Message], config: StrandsPromptConfig
) -> EncodedStrandsPrompt:
    if not messages or messages[-1].role != "user":
        raise ValueError("Strands combined prompts must end in an original user request")
    if not tokenizer.is_fast or not tokenizer.chat_template:
        raise ValueError("Strands requires a native chat template and fast offset tokenizer")
    block = "\n\n<edge_router_requirements>\n"
    spans = []
    for requirement in [config.complexity, config.sensitivity]:
        question, positions = _question(requirement)
        start = len(block)
        spans.extend((start + a, start + b) for a, b in positions)
        block += question + "\n</answer>\n"
    block += "</edge_router_requirements>\n" + config.generation_instruction.strip()
    formatted = [message.model_dump() for message in messages]
    formatted[-1]["content"] += block
    text = tokenizer.apply_chat_template(
        formatted, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    # Last exact occurrence belongs to our appended block, even if user text mimics it.
    base = text.rfind(block)
    if base < 0 or base + len(block) >= len(text):
        raise ValueError("Chat template must preserve the appended block and assistant suffix")
    encoded = tokenizer(
        text, add_special_tokens=False, return_tensors="pt", return_offsets_mapping=True
    )
    offsets = encoded.pop("offset_mapping")[0].tolist()
    positions = []
    for start, end in spans:
        start, end = base + start, base + end
        indices = [i for i, (a, b) in enumerate(offsets) if a < end and b > start]
        if not indices:
            raise ValueError("Decision marker or option has no token representation")
        positions.append(indices[-1])
    if len(positions) != 7 or max(positions) >= encoded["input_ids"].shape[1] - 1:
        raise ValueError("Decision positions must precede the final generation position")
    return EncodedStrandsPrompt(encoded["input_ids"], encoded["attention_mask"], tuple(positions))
