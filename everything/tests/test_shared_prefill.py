import pytest
from conftest import fixed_heads

from edge_router.schemas import ChatRequest, DecisionRequest, Message


def test_prompt_processed_exactly_once(service, backend):
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content="Hello")], generation={"max_new_tokens": 4}
        )
    )
    assert result.route == "LOCAL"
    assert backend.instrumentation.prefill_calls == 1
    assert backend.forward_trace[0] == ("prefill", result.decision.prompt_tokens)
    assert all(kind == "decode" and length == 1 for kind, length in backend.forward_trace[1:])
    assert backend.instrumentation.prompt_tokens == result.decision.prompt_tokens
    assert backend.instrumentation.decoded_input_tokens == result.generated_tokens - 1
    assert backend.instrumentation.backbone_instances == 1


@pytest.mark.parametrize("complexity,route", [("MEDIUM", "PRIVATE_AWS"), ("HIGH", "FRONTIER")])
def test_handoff_performs_no_generation(service, backend, complexity, route):
    backend.head_manager.activate(fixed_heads(backend, complexity=complexity))
    result = service.run(
        ChatRequest(messages=[Message(role="user", content="Synthetic public task")])
    )
    assert result.status == "HANDOFF_REQUIRED" and result.route == route
    assert result.text is None
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.decode_calls == 0
    assert len(backend.forward_trace) == 1


def test_decision_only_does_not_decode(service, backend):
    service.decide(DecisionRequest(messages=[Message(role="user", content="Hello")]))
    assert backend.instrumentation.prefill_calls == 1
    assert backend.instrumentation.decode_calls == 0


def test_context_limits_are_explicit(service):
    with pytest.raises(ValueError):
        service.run(ChatRequest(messages=[Message(role="user", content="x" * 2048)]))
