import threading
from collections import Counter
from typing import Protocol


class Metrics(Protocol):
    def observe(self, name: str, value: float, labels: dict[str, str] | None = None): ...


class InMemoryMetrics:
    """Bounded aggregates. Never retain examples or per-request content."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sum = Counter()
        self._count = Counter()

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None):
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._sum[key] += value
            self._count[key] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                str(key): {"sum": value, "count": self._count[key]}
                for key, value in self._sum.items()
            }
