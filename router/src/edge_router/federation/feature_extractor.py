import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import torch

from edge_router.feedback.models import TRUSTED_SENSITIVITY_SOURCES, LocalExample
from edge_router.model.backend import SharedBackboneBackend
from edge_router.schemas import Complexity, Sensitivity


@dataclass
class LocalFeature:
    """Potentially sensitive. Never accepted by a FederationAdapter."""

    vector: torch.Tensor
    complexity_index: int = -1
    sensitivity_index: int = -1
    backbone_identity_hash: str = ""


class FeatureStore(Protocol):
    """An approved local persistence implementation must enforce encryption/retention.

    Features are not anonymized. This milestone ships only bounded in-memory storage.
    """

    def put(self, key: str, feature: LocalFeature): ...
    def get(self, key: str) -> LocalFeature | None: ...


def feature_identity(identity: dict) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class InMemoryFeatureStore:
    """Optional ephemeral cache with explicit bounded retention; no disk writes."""

    def __init__(self, retention_seconds: float = 3600, capacity: int = 1000):
        if retention_seconds <= 0 or capacity < 1:
            raise ValueError("Feature retention must be bounded")
        self.ttl, self.capacity = retention_seconds, capacity
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self):
        for key in list(self._items):
            if self._items[key][0] < monotonic() - self.ttl:
                del self._items[key]
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    @staticmethod
    def _copy(value: LocalFeature):
        return LocalFeature(
            value.vector.detach().clone(),
            value.complexity_index,
            value.sensitivity_index,
            value.backbone_identity_hash,
        )

    def put(self, key: str, feature: LocalFeature):
        with self._lock:
            self._items[key] = (monotonic(), self._copy(feature))
            self._expire()

    def get(self, key: str) -> LocalFeature | None:
        with self._lock:
            self._expire()
            result = self._items.get(key)
            return self._copy(result[1]) if result else None


class FrozenFeatureExtractor:
    def __init__(self, backend: SharedBackboneBackend):
        self.backend = backend

    def extract(self, example: LocalExample) -> LocalFeature:
        with self.backend.execution_lock, torch.inference_mode():
            context = self.backend.prefill(example.messages)
            value = context.metadata.get("decision_features", context.final_hidden_state).detach()
        # Clone OUTSIDE inference_mode, since autograd needs to save head inputs.
        vector = value.float().clone().squeeze(0)
        complexity = (
            list(Complexity).index(example.complexity) if example.complexity is not None else -1
        )
        sensitivity = -1
        if example.sensitivity is not None and example.label_source in TRUSTED_SENSITIVITY_SOURCES:
            sensitivity = list(Sensitivity).index(example.sensitivity)
        return LocalFeature(
            vector, complexity, sensitivity, feature_identity(self.backend.identity)
        )
