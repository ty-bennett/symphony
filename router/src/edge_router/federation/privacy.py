from pydantic import Field

from edge_router.schemas import StrictModel


class PrivacyConfig(StrictModel):
    differential_privacy_enabled: bool = False
    max_grad_norm: float | None = Field(default=None, gt=0)
    noise_multiplier: float | None = Field(default=None, gt=0)
    target_delta: float | None = Field(default=None, gt=0, lt=1)

    def ensure_supported(self):
        if self.differential_privacy_enabled:
            raise NotImplementedError(
                "DP requires an Opacus integration and privacy accountant; no DP claim is made"
            )
