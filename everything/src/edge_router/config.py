import os
from pathlib import Path

import yaml
from pydantic import Field

from edge_router.schemas import StrictModel


class Settings(StrictModel):
    model_id: str = "google/gemma-3-4b-it"
    model_revision: str = "main"
    model_backend: str = "gemma3"
    device: str = "cpu"
    dtype: str = "float32"
    quantization: str = "none"
    quantized_model_path: str | None = None
    decision_checkpoint: str | None = None
    policy_config: str = "config/routing_policy.yaml"
    max_context_tokens: int = Field(default=2048, ge=2)
    default_max_new_tokens: int = Field(default=128, ge=1)
    log_level: str = "INFO"
    unsafe_content_logging: bool = False
    federation_enabled: bool = False
    local_files_only: bool = False
    strands_checkpoint: str = "StrandsAgents/strands-decider-E4B-gemma4-v1-2610"
    strands_revision: str = "main"
    strands_prompt_config: str = "config/strands_requirements.yaml"

    @classmethod
    def from_env(cls) -> "Settings":
        values = {
            name: os.environ[name.upper()]
            for name in cls.model_fields
            if name.upper() in os.environ
        }
        if not values.get("decision_checkpoint"):
            values.pop("decision_checkpoint", None)
        return cls.model_validate(values)


def read_yaml(path: str | Path) -> dict:
    with Path(path).open() as file:
        value = yaml.safe_load(file)
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a YAML mapping")
    return value
