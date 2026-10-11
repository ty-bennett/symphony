from collections.abc import Iterator
from time import perf_counter
from uuid import uuid4

from edge_router.model.backend import SharedBackboneBackend
from edge_router.model.memory import synchronize
from edge_router.observability.logging import log_event
from edge_router.observability.metrics import InMemoryMetrics, Metrics
from edge_router.routing.difficulty_policy import DifficultyRoutingPolicy
from edge_router.routing.policy import RoutingPolicy
from edge_router.routing.security_scanner import SecurityScanner
from edge_router.schemas import (
    ChatRequest,
    ChatResult,
    DecisionRequest,
    DecisionResult,
    DifficultyPrediction,
    GenerationOptions,
    Route,
    StreamEvent,
)
from edge_router.service.executors import HandoffExecutor, LocalExecutor, RemoteExecutor


class Orchestrator:
    def __init__(
        self,
        backend: SharedBackboneBackend,
        policy: RoutingPolicy | DifficultyRoutingPolicy,
        scanner: SecurityScanner | None = None,
        remote: RemoteExecutor | None = None,
        metrics: Metrics | None = None,
        default_max_new_tokens: int = 128,
        unsafe_content_logging: bool = False,
    ):
        self.backend, self.policy = backend, policy
        self.scanner, self.remote = scanner or SecurityScanner(), remote or HandoffExecutor()
        self.local = LocalExecutor(backend)
        self.metrics = metrics or InMemoryMetrics()
        self.default_max_new_tokens = default_max_new_tokens
        self.unsafe_content_logging = unsafe_content_logging

    def _decide(self, request: DecisionRequest):
        request_id = str(uuid4())
        # Snapshot once before prefill: a swap cannot mix versions in a request.
        snapshot = self.backend.head_manager.snapshot()
        flags = self.scanner.scan(request.messages)
        synchronize()
        start = perf_counter()
        context = self.backend.prefill(request.messages)
        synchronize()
        prefill_ms = (perf_counter() - start) * 1000
        start = perf_counter()
        prediction = self.backend.classify(context, snapshot)
        complexity, sensitivity, difficulty = None, None, None
        if isinstance(prediction, DifficultyPrediction):
            if not isinstance(self.policy, DifficultyRoutingPolicy):
                raise TypeError("Regression heads require a difficulty routing policy")
            difficulty = prediction
            choice = self.policy.decide(difficulty, flags, request.security_context)
        else:
            if not isinstance(self.policy, RoutingPolicy):
                raise TypeError("Classifier heads require a classification routing policy")
            complexity, sensitivity = prediction
            choice = self.policy.decide(complexity, sensitivity, flags, request.security_context)
        result = DecisionResult(
            request_id=request_id,
            complexity=complexity,
            sensitivity=sensitivity,
            difficulty=difficulty,
            resolved_sensitivity=choice.resolved_sensitivity,
            route=choice.route,
            policy_reason=choice.reason,
            security_flags=flags,
            timing_ms={"prefill": prefill_ms, "decision": (perf_counter() - start) * 1000},
            prompt_tokens=context.prompt_length,
            decision_model_version=snapshot.version,
        )
        return result, context

    def decide(self, request: DecisionRequest) -> DecisionResult:
        start = perf_counter()
        with self.backend.execution_lock:
            result, _ = self._decide(request)
        self._observe(result, 0, 0, start)
        return result

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        start = perf_counter()
        # Batch size 1: serialize model/cache work across inference and feature extraction.
        # Head optimization has no dependency on this lock and may run concurrently.
        with self.backend.execution_lock:
            decision, context = self._decide(request)
            yield StreamEvent(event="decision", data=decision.model_dump(mode="json"))
            if decision.route != Route.LOCAL:
                del context  # Release GPU cache before any future remote handoff.
                result = self.remote.execute(request, decision)
                self._observe(decision, 0, 0, start)
                yield StreamEvent(event="complete", data=result.model_dump(mode="json"))
                return
            options = request.generation or GenerationOptions(
                max_new_tokens=self.default_max_new_tokens
            )
            generation_start = perf_counter()
            count, pieces, finish = 0, [], "length"
            for token in self.local.execute(context, options):
                if count == 0:
                    decision.timing_ms["time_to_first_token"] = (perf_counter() - start) * 1000
                count += 1
                if token.text:
                    pieces.append(token.text)
                    yield StreamEvent(event="token", data={"text": token.text})
                if token.eos:
                    finish = "eos"
                    break
            generation_ms = (perf_counter() - generation_start) * 1000
            decision.timing_ms["generation"] = generation_ms
            result = ChatResult(
                status="COMPLETED",
                route=decision.route,
                request_id=decision.request_id,
                decision=decision,
                text="".join(pieces),
                generated_tokens=count,
                finish_reason=finish,
            )
            self._observe(decision, count, generation_ms, start)
            if self.unsafe_content_logging:
                log_event(
                    "UNSAFE_CONTENT_LOGGING",
                    messages=[m.model_dump() for m in request.messages],
                    response=result.text,
                )
            yield StreamEvent(event="complete", data=result.model_dump(mode="json"))

    def run(self, request: ChatRequest) -> ChatResult:
        for event in self.stream(request):
            if event.event == "complete":
                return ChatResult.model_validate(event.data)
        raise RuntimeError("Generation ended without a completion event")

    def _observe(
        self, decision: DecisionResult, generated: int, generation_ms: float, start: float
    ):
        fields = {
            "request_id": decision.request_id,
            "route": decision.route.value,
            "complexity": decision.complexity.label.value if decision.complexity else None,
            "complexity_confidence": decision.complexity.confidence
            if decision.complexity
            else None,
            "sensitivity": decision.sensitivity.label.value if decision.sensitivity else None,
            "sensitivity_confidence": decision.sensitivity.confidence
            if decision.sensitivity
            else None,
            "difficulty_score": decision.difficulty.score if decision.difficulty else None,
            "resolved_sensitivity": decision.resolved_sensitivity.value,
            "decision_model_version": decision.decision_model_version,
            "prompt_tokens": decision.prompt_tokens,
            "generated_tokens": generated,
            "prefill_ms": decision.timing_ms["prefill"],
            "decision_ms": decision.timing_ms["decision"],
            "generation_ms": generation_ms,
            "total_ms": (perf_counter() - start) * 1000,
        }
        log_event("request_completed", **fields)
        self.metrics.observe("requests", 1, {"route": decision.route.value})
        for name in ["prefill_ms", "decision_ms", "generation_ms", "total_ms"]:
            self.metrics.observe(name, fields[name])
