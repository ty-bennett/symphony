import logging

import pytest
from conftest import fixed_heads

from edge_router.routing.security_scanner import SecurityScanner
from edge_router.schemas import ChatRequest, Message


@pytest.mark.parametrize(
    "content,flag",
    [
        ("-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----", "PRIVATE_KEY"),
        ("AKIA1234567890ABCDEF", "AWS_ACCESS_KEY"),
        ("Authorization: Bearer fake_test_token_12345", "BEARER_TOKEN"),
        ('password="synthetic_test_only"', "LABELLED_SECRET"),
        ('"api_key": "synthetic_test_only"', "LABELLED_SECRET"),
        ("aws_secret_access_key=synthetic_test_only", "LABELLED_SECRET"),
        ("123-45-6789", "SSN_PATTERN"),
        ("postgres://user:synthetic@localhost/db", "CREDENTIAL_URL"),
    ],
)
def test_patterns(content, flag):
    flags = SecurityScanner().scan([Message(role="user", content=content)])
    assert flag in flags
    assert all(content not in code for code in flags)


def test_detection_overrides_wrong_federated_public_head(service, backend):
    backend.head_manager.activate(fixed_heads(backend, complexity="HIGH", sensitivity="PUBLIC"))
    result = service.run(
        ChatRequest(messages=[Message(role="user", content="password=synthetic_test_secret")])
    )
    assert result.route == "PRIVATE_AWS"
    assert result.status == "HANDOFF_REQUIRED"
    assert backend.instrumentation.decode_calls == 0


def test_normal_logs_never_include_prompt_secret_or_response(service, caplog):
    caplog.set_level(logging.INFO, logger="edge_router")
    content = "password=synthetic_canary_do_not_log"
    result = service.run(
        ChatRequest(
            messages=[Message(role="user", content=content)], generation={"max_new_tokens": 3}
        )
    )
    assert caplog.records
    combined = repr([(r.getMessage(), getattr(r, "safe_fields", {})) for r in caplog.records])
    assert content not in combined
    assert "synthetic_canary" not in combined
    assert "messages" not in combined and "response" not in combined
    assert result.request_id in combined
