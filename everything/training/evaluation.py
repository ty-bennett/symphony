import torch
from torch import nn

from edge_router.federation.feature_extractor import FrozenFeatureExtractor
from edge_router.feedback.models import LocalExample
from edge_router.model.decision_heads import HeadSnapshot, predict
from edge_router.routing.policy import RoutingPolicy
from edge_router.routing.security_scanner import SecurityScanner
from edge_router.schemas import Classification, Complexity, Route, SecurityContext, Sensitivity


def classification_metrics(truth: list[int], prediction: list[int], labels: list[str]) -> dict:
    matrix = [[0 for _ in labels] for _ in labels]
    for y, p in zip(truth, prediction, strict=True):
        matrix[y][p] += 1
    precision, recall = {}, {}
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        precision[label] = tp / max(1, sum(row[i] for row in matrix))
        recall[label] = tp / max(1, sum(matrix[i]))
    return {
        "accuracy": sum(matrix[i][i] for i in range(len(labels))) / max(1, len(truth)),
        "precision": precision,
        "recall": recall,
        "confusion_matrix": matrix,
        "label_order": labels,
        "examples": len(truth),
    }


def evaluate_heads(
    snapshot: HeadSnapshot,
    extractor: FrozenFeatureExtractor,
    examples: list[LocalExample],
    policy: RoutingPolicy,
) -> dict:
    cy, cp, sy, sp = [], [], [], []
    route_correct, route_count, violations = 0, 0, 0
    scanner = SecurityScanner()
    for example in examples:
        feature = extractor.extract(example)
        device = next(snapshot.heads.parameters()).device
        complexity, sensitivity = predict(snapshot, feature.vector.unsqueeze(0).to(device))
        if feature.complexity_index >= 0:
            cy.append(feature.complexity_index)
            cp.append(list(Complexity).index(complexity.label))
        if feature.sensitivity_index >= 0:
            sy.append(feature.sensitivity_index)
            sp.append(list(Sensitivity).index(sensitivity.label))
        # Ground-truth sensitivity is NOT injected as trusted request metadata here:
        # this measures actual routing using the metadata present in the dataset.
        choice = policy.decide(
            complexity, sensitivity, scanner.scan(example.messages), example.security_context
        )
        if example.route:
            route_count += 1
            route_correct += choice.route.value == example.route
        if example.sensitivity == Sensitivity.SENSITIVE and choice.route == Route.FRONTIER:
            violations += 1
    cm = classification_metrics(cy, cp, list(Complexity))
    sm = classification_metrics(sy, sp, list(Sensitivity))
    sm["sensitive_false_negative_rate"] = (
        1 - sm["recall"][Sensitivity.SENSITIVE] if 1 in sy else None
    )
    sm["false_positive_rate"] = sm["confusion_matrix"][0][1] / max(1, sy.count(0))
    # Independently test the hard invariant with a trusted SENSITIVE escalation on all
    # examples. Learned errors without deterministic evidence are reported above.
    enforced_violations = 0
    for c_label in Complexity:
        from edge_router.schemas import ComplexityPrediction, SensitivityPrediction

        c = ComplexityPrediction(
            label=c_label, confidence=1, probabilities={x: float(x == c_label) for x in Complexity}
        )
        s = SensitivityPrediction(
            label=Sensitivity.PUBLIC,
            confidence=1,
            probabilities={Sensitivity.PUBLIC: 1, Sensitivity.SENSITIVE: 0},
        )
        choice = policy.decide(c, s, [], SecurityContext(classification=Classification.SENSITIVE))
        enforced_violations += choice.route == Route.FRONTIER
    return {
        "complexity": cm,
        "sensitivity": sm,
        "routing": {
            "accuracy": route_correct / route_count if route_count else None,
            "labelled_examples": route_count,
            "sensitive_examples_allowed_frontier": violations,
            "deterministic_invariant_violations": enforced_violations,
        },
    }


def calibrate_temperature(logits: torch.Tensor, labels: torch.Tensor) -> float:
    """Fit a single positive temperature on held-out labels; never a policy threshold."""
    logits, labels = logits.detach().float(), labels.detach()
    log_temperature = nn.Parameter(torch.zeros((), device=logits.device))
    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=50)

    def closure():
        optimizer.zero_grad()
        loss = nn.functional.cross_entropy(logits / log_temperature.exp().clamp(0.05, 20), labels)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_temperature.detach().exp().clamp(0.05, 20))
