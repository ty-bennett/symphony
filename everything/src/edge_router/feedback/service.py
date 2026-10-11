from edge_router.feedback.models import TRUSTED_SENSITIVITY_SOURCES, FeedbackRecord, LocalExample
from edge_router.feedback.store import LocalDataStore


class FeedbackService:
    def __init__(self, store: LocalDataStore):
        self.store = store

    def record(self, feedback: FeedbackRecord) -> LocalExample:
        if (
            feedback.corrected_sensitivity is not None
            and feedback.label_source not in TRUSTED_SENSITIVITY_SOURCES
        ):
            raise ValueError("Sensitivity corrections require a trusted label source")
        request = self.store.get(feedback.request_id)
        if request is None:
            raise ValueError("Original request is not retained in the approved local store")
        example = LocalExample(
            **request.model_dump(),
            complexity=feedback.corrected_complexity,
            sensitivity=feedback.corrected_sensitivity,
            label_source=feedback.label_source,
            timestamp=feedback.timestamp,
        )
        self.store.add_example(example)
        return example
