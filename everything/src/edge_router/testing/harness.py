import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

import torch
from pydantic import Field

from edge_router.model.memory import synchronize
from edge_router.schemas import (
    ChatRequest,
    ChatResult,
    Complexity,
    DecisionRequest,
    DecisionResult,
    GenerationOptions,
    Message,
    Route,
    SecurityContext,
    Sensitivity,
    StreamEvent,
    StrictModel,
)
from edge_router.service.orchestrator import Orchestrator


class EvaluationCase(StrictModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    security_context: SecurityContext = Field(default_factory=SecurityContext)
    # Ground truth is only used for scoring, never injected into routing metadata.
    complexity: Complexity | None = None
    sensitivity: Sensitivity | None = None
    route: Route | None = None
    difficulty: float | None = Field(default=None, allow_inf_nan=False)


@dataclass
class TestOutcome:
    decision: DecisionResult
    status: str
    generated_tokens: int
    total_ms: float
    instrumentation: dict
    shared_prefill_verified: bool
    peak_cuda_memory_bytes: int | None

    def safe_record(self) -> dict:
        generation_ms = self.decision.timing_ms.get("generation", 0)
        return {
            "decision": self.decision.model_dump(mode="json"),
            "status": self.status,
            "generated_tokens": self.generated_tokens,
            "total_ms": self.total_ms,
            "time_to_first_token_ms": self.decision.timing_ms.get("time_to_first_token"),
            "decode_tokens_per_second": self.generated_tokens * 1000 / generation_ms
            if generation_ms > 0
            else None,
            "instrumentation": self.instrumentation,
            "shared_prefill_verified": self.shared_prefill_verified,
            "peak_cuda_memory_bytes": self.peak_cuda_memory_bytes,
        }


class ModelTestHarness:
    """Use the production orchestrator; no forced routes or second prompt prefill."""

    def __init__(self, service: Orchestrator):
        if service.unsafe_content_logging:
            raise ValueError("Test harness requires content logging disabled")
        self.service = service

    def run(
        self,
        case: EvaluationCase,
        options: GenerationOptions,
        mode: str = "chat",
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> TestOutcome:
        if mode not in {"chat", "decision"}:
            raise ValueError("Unsupported testing mode")
        backend = self.service.backend
        device = str(backend.model_info().get("device", "cpu"))
        use_cuda = device.startswith("cuda") and torch.cuda.is_available()
        with backend.execution_lock:
            before = backend.instrumentation.snapshot()
            if use_cuda:
                torch.cuda.reset_peak_memory_stats(backend.device)
            synchronize()
            start = perf_counter()
            if mode == "decision":
                decision = self.service.decide(
                    DecisionRequest(messages=case.messages, security_context=case.security_context)
                )
                count, status = 0, "DECISION_ONLY"
                if on_event:
                    on_event(StreamEvent(event="decision", data=decision.model_dump(mode="json")))
            else:
                complete = None
                request = ChatRequest(
                    messages=case.messages,
                    security_context=case.security_context,
                    generation=options,
                )
                for event in self.service.stream(request):
                    if on_event:
                        on_event(event)
                    if event.event == "complete":
                        complete = ChatResult.model_validate(event.data)
                if complete is None:
                    raise RuntimeError("Testing stream ended without a completion")
                decision, count, status = (
                    complete.decision,
                    complete.generated_tokens,
                    complete.status,
                )
            synchronize()
            total_ms = (perf_counter() - start) * 1000
            after = backend.instrumentation.snapshot()
            delta = {
                key: after[key] - before[key]
                for key in [
                    "prefill_calls",
                    "prompt_tokens",
                    "decode_calls",
                    "decoded_input_tokens",
                ]
            }
            delta["backbone_instances"] = after["backbone_instances"]
            verified = (
                delta["prefill_calls"] == 1
                and delta["prompt_tokens"] == decision.prompt_tokens
                and delta["decoded_input_tokens"] == max(0, count - 1)
                and delta["decode_calls"] == max(0, count - 1)
                and delta["backbone_instances"] == 1
                and (decision.route == Route.LOCAL or count == 0)
            )
            if not verified:
                raise RuntimeError("Shared-prefill or route-execution audit failed")
            peak = torch.cuda.max_memory_allocated(backend.device) if use_cuda else None
        return TestOutcome(decision, status, count, total_ms, delta, verified, peak)


def classification_metrics(truth: list[str], predictions: list[str], labels: list[str]) -> dict:
    matrix = [[0 for _ in labels] for _ in labels]
    for expected, actual in zip(truth, predictions, strict=True):
        matrix[labels.index(expected)][labels.index(actual)] += 1
    precision, recall = {}, {}
    for i, label in enumerate(labels):
        predicted_count, support = sum(row[i] for row in matrix), sum(matrix[i])
        precision[label] = matrix[i][i] / predicted_count if predicted_count else None
        recall[label] = matrix[i][i] / support if support else None
    return {
        "examples": len(truth),
        "accuracy": sum(matrix[i][i] for i in range(len(labels))) / len(truth) if truth else None,
        "precision": precision,
        "recall": recall,
        "confusion_matrix": matrix,
        "label_order": labels,
    }


def summarize(cases: list[EvaluationCase], outcomes: list[TestOutcome], model_info: dict) -> dict:
    complexity_pairs, sensitivity_pairs, difficulty_errors = [], [], []
    route_correct, route_count, sensitive_frontier, deterministic_violations = 0, 0, 0, 0
    for case, outcome in zip(cases, outcomes, strict=True):
        decision = outcome.decision
        if case.difficulty is not None and decision.difficulty is not None:
            difficulty_errors.append(decision.difficulty.score - case.difficulty)
        if case.complexity is not None and decision.complexity is not None:
            complexity_pairs.append((case.complexity.value, decision.complexity.label.value))
        if case.sensitivity is not None and decision.sensitivity is not None:
            sensitivity_pairs.append((case.sensitivity.value, decision.sensitivity.label.value))
        if case.route is not None:
            route_count += 1
            route_correct += case.route == decision.route
        sensitive_frontier += (
            case.sensitivity == Sensitivity.SENSITIVE and decision.route == Route.FRONTIER
        )
        deterministic_violations += (
            decision.resolved_sensitivity == Sensitivity.SENSITIVE
            and decision.route == Route.FRONTIER
        )

    def score(pairs, labels):
        return classification_metrics([p[0] for p in pairs], [p[1] for p in pairs], labels)

    complexity = score(complexity_pairs, list(Complexity))
    sensitivity = score(sensitivity_pairs, list(Sensitivity))
    recall = sensitivity["recall"][Sensitivity.SENSITIVE]
    sensitivity["sensitive_false_negative_rate"] = 1 - recall if recall is not None else None
    public_count = sum(sensitivity["confusion_matrix"][0])
    sensitivity["false_positive_rate"] = (
        sensitivity["confusion_matrix"][0][1] / public_count if public_count else None
    )
    latencies = sorted(o.total_ms for o in outcomes)
    ttft = [
        o.decision.timing_ms["time_to_first_token"]
        for o in outcomes
        if "time_to_first_token" in o.decision.timing_ms
    ]
    decode_rates = [
        o.safe_record()["decode_tokens_per_second"] for o in outcomes if o.generated_tokens > 0
    ]
    peaks = [o.peak_cuda_memory_bytes for o in outcomes if o.peak_cuda_memory_bytes is not None]
    demo = model_info.get("backend") == "tiny-development-only"
    return {
        "model": model_info,
        "quality_test_valid": not demo and model_info.get("decision_checkpoint_loaded", True),
        "warning": "OFFLINE DEMO: not Gemma/Strands quality or deployment performance"
        if demo
        else "Small/manual datasets and unvalidated probability calibration are not production-quality evidence",
        "requests": len(outcomes),
        "complexity": complexity,
        "sensitivity": sensitivity,
        "difficulty": {
            "labelled_examples": len(difficulty_errors),
            "rmse": math.sqrt(statistics.mean(e * e for e in difficulty_errors))
            if difficulty_errors
            else None,
            "mae": statistics.mean(abs(e) for e in difficulty_errors)
            if difficulty_errors
            else None,
        },
        "routing": {
            "labelled_examples": route_count,
            "accuracy": route_correct / route_count if route_count else None,
            "ground_truth_sensitive_examples_allowed_frontier": sensitive_frontier,
            "deterministic_invariant_violations": deterministic_violations,
        },
        "performance": {
            "mean_total_ms": statistics.mean(latencies) if latencies else None,
            "p50_total_ms": statistics.median(latencies) if latencies else None,
            "p95_total_ms": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)]
            if latencies
            else None,
            "shared_prefill_verified_requests": sum(o.shared_prefill_verified for o in outcomes),
            "mean_prefill_ms": statistics.mean(o.decision.timing_ms["prefill"] for o in outcomes)
            if outcomes
            else None,
            "mean_decision_ms": statistics.mean(o.decision.timing_ms["decision"] for o in outcomes)
            if outcomes
            else None,
            "mean_time_to_first_token_ms": statistics.mean(ttft) if ttft else None,
            "mean_local_decode_tokens_per_second": statistics.mean(decode_rates)
            if decode_rates
            else None,
            "peak_cuda_memory_bytes": max(peaks) if peaks else None,
        },
        # No prompts, generated responses, hidden vectors, token IDs or raw matches.
        "results": [o.safe_record() for o in outcomes],
    }
