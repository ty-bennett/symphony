from conftest import fixed_heads
from fastapi.testclient import TestClient

from edge_router.api.app import create_app


def test_api(service, backend):
    with TestClient(create_app(service)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/v1/model/info").json()["backbone_instances"] == 1
        payload = {"messages": [{"role": "user", "content": "Hello"}]}
        decision = client.post("/v1/decision", json=payload)
        assert decision.status_code == 200
        assert "probabilities" in decision.json()["sensitivity"]
        chat = client.post("/v1/chat", json={**payload, "generation": {"max_new_tokens": 3}})
        assert chat.status_code == 200 and chat.json()["status"] == "COMPLETED"
        backend.head_manager.activate(fixed_heads(backend, complexity="HIGH"))
        handoff = client.post("/v1/chat", json=payload).json()
        assert handoff["route"] == "FRONTIER" and handoff["status"] == "HANDOFF_REQUIRED"


def test_streaming(service):
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "generation": {"max_new_tokens": 3},
            },
        )
        assert response.status_code == 200
        assert "event: decision" in response.text
        assert "event: token" in response.text
        assert "event: complete" in response.text
        assert "hidden" not in response.text and "token_id" not in response.text


def test_validation_error_does_not_echo_secret(service):
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/v1/chat", json={"messages": [{"role": "canary_secret", "content": "test"}]}
        )
        assert response.status_code == 422
        assert "canary_secret" not in response.text


def test_library_error_is_sanitized_before_server_logging(service, backend, caplog):
    def failing_prefill(messages):
        raise RuntimeError("synthetic_private_canary_from_tokenizer")

    backend.prefill = failing_prefill
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/v1/decision", json={"messages": [{"role": "user", "content": "Hello"}]}
        )
    assert response.status_code == 500
    assert "synthetic_private_canary" not in response.text
    assert "synthetic_private_canary" not in repr(
        [(r.getMessage(), r.exc_info) for r in caplog.records]
    )
