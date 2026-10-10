import threading
from collections import OrderedDict
from time import monotonic
from typing import Protocol

from edge_router.feedback.models import LocalExample
from edge_router.schemas import DecisionRequest


class LocalDataStore(Protocol):
    """Deployment supplies approved/encrypted persistence. No federation accessor."""

    def retain(self, request_id: str, request: DecisionRequest): ...
    def get(self, request_id: str) -> DecisionRequest | None: ...
    def add_example(self, example: LocalExample): ...
    def examples(self) -> list[LocalExample]: ...


class InMemoryLocalStore:
    def __init__(
        self, retention_enabled: bool = False, ttl_seconds: float = 3600, capacity: int = 1000
    ):
        if ttl_seconds <= 0 or capacity < 1:
            raise ValueError("Retention must be bounded")
        self.enabled, self.ttl, self.capacity = retention_enabled, ttl_seconds, capacity
        self._requests, self._examples = OrderedDict(), OrderedDict()
        self._lock = threading.Lock()

    def _expire(self, items):
        cutoff = monotonic() - self.ttl
        for key in list(items):
            if items[key][0] < cutoff:
                del items[key]
        while len(items) > self.capacity:
            items.popitem(last=False)

    def retain(self, request_id: str, request: DecisionRequest):
        if not self.enabled:
            return
        with self._lock:
            self._requests[request_id] = (monotonic(), request.model_copy(deep=True))
            self._expire(self._requests)

    def get(self, request_id: str):
        with self._lock:
            self._expire(self._requests)
            value = self._requests.get(request_id)
            return value[1].model_copy(deep=True) if value else None

    def add_example(self, example: LocalExample):
        if not self.enabled:
            raise ValueError("Local retention is disabled")
        with self._lock:
            self._examples[len(self._examples), monotonic()] = (
                monotonic(),
                example.model_copy(deep=True),
            )
            self._expire(self._examples)

    def examples(self):
        with self._lock:
            self._expire(self._examples)
            return [item[1].model_copy(deep=True) for item in self._examples.values()]
