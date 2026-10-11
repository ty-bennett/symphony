import os

import pytest

from edge_router.api.app import build_orchestrator
from edge_router.config import Settings
from edge_router.schemas import ChatRequest, Message


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_REAL_MODEL_TESTS") != "1", reason="Opt in to model downloads"
)
def test_configured_real_model_local_generation():
    service = build_orchestrator(Settings.from_env())
    before = service.backend.instrumentation.prefill_calls
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="Say hello.")],
            generation={"max_new_tokens": 3},
            security_context={"classification": "SENSITIVE"},
        )
    )
    assert service.backend.instrumentation.prefill_calls - before == 1
    if result.route == "LOCAL":
        assert result.status == "COMPLETED" and result.generated_tokens >= 1
    else:
        assert result.status == "HANDOFF_REQUIRED"
