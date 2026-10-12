import pytest

from edge_router.feedback.models import FeedbackRecord
from edge_router.feedback.service import FeedbackService
from edge_router.feedback.store import InMemoryLocalStore
from edge_router.schemas import DecisionRequest, Message


def test_retention_is_explicit_and_feedback_local():
    request = DecisionRequest(messages=[Message(role="user", content="Synthetic example")])
    disabled = InMemoryLocalStore()
    disabled.retain("1", request)
    assert disabled.get("1") is None
    store = InMemoryLocalStore(retention_enabled=True)
    store.retain("1", request)
    service = FeedbackService(store)
    example = service.record(
        FeedbackRecord(
            request_id="1", corrected_sensitivity="SENSITIVE", label_source="HUMAN_CONFIRMED"
        )
    )
    assert example.sensitivity == "SENSITIVE"
    with pytest.raises(ValueError):
        service.record(
            FeedbackRecord(
                request_id="1", corrected_sensitivity="PUBLIC", label_source="ROUTE_OUTCOME"
            )
        )
