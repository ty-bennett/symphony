import itertools

import pytest

from edge_router.routing.policy import PolicyConfig
from edge_router.schemas import (
    Classification,
    Complexity,
    ComplexityPrediction,
    Route,
    SecurityContext,
    Sensitivity,
    SensitivityPrediction,
)


def predictions(complexity="HIGH", public=0.99):
    c = ComplexityPrediction(
        label=complexity,
        confidence=1,
        probabilities={x: float(x == complexity) for x in Complexity},
    )
    s = SensitivityPrediction(
        label="PUBLIC" if public >= 0.5 else "SENSITIVE",
        confidence=max(public, 1 - public),
        probabilities={"PUBLIC": public, "SENSITIVE": 1 - public},
    )
    return c, s


@pytest.mark.parametrize(
    "sensitivity,complexity,route",
    [
        ("PUBLIC", "LOW", "LOCAL"),
        ("PUBLIC", "MEDIUM", "PRIVATE_AWS"),
        ("PUBLIC", "HIGH", "FRONTIER"),
        ("SENSITIVE", "LOW", "LOCAL"),
        ("SENSITIVE", "MEDIUM", "PRIVATE_AWS"),
        ("SENSITIVE", "HIGH", "PRIVATE_AWS"),
    ],
)
def test_default_matrix(policy, sensitivity, complexity, route):
    c, s = predictions(complexity, 0.99 if sensitivity == "PUBLIC" else 0.01)
    assert policy.decide(c, s, [], SecurityContext()).route == route


@pytest.mark.parametrize("public", [0.5, 0.6, 0.79, 0.94, float("nan")])
def test_low_public_confidence_cannot_reach_frontier(policy, public):
    c, s = predictions(public=public)
    assert policy.decide(c, s, [], SecurityContext()).route == Route.PRIVATE_AWS


def test_sensitive_signal_always_blocks_frontier(policy):
    for complexity, classification, flag, public in itertools.product(
        Complexity, Classification, [False, True], [0.01, 0.5, 0.99]
    ):
        c, s = predictions(complexity, public)
        result = policy.decide(
            c, s, ["PRIVATE_KEY"] if flag else [], SecurityContext(classification=classification)
        )
        if flag or classification == Classification.SENSITIVE or s.label == Sensitivity.SENSITIVE:
            assert result.route != Route.FRONTIER
            assert result.resolved_sensitivity == Sensitivity.SENSITIVE


def test_public_metadata_cannot_downgrade(policy):
    c, s = predictions(public=0.01)
    assert (
        policy.decide(c, s, [], SecurityContext(classification="PUBLIC")).route == Route.PRIVATE_AWS
    )


def test_malicious_policy_matrix_rejected(policy):
    data = policy.config.model_dump()
    data["matrix"][Sensitivity.SENSITIVE][Complexity.HIGH] = Route.FRONTIER
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(data)
    data = policy.config.model_dump()
    data["uncertain_route"] = Route.FRONTIER
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(data)


def test_policy_change_requires_no_retraining(policy):
    policy.config.matrix[Sensitivity.PUBLIC][Complexity.HIGH] = Route.LOCAL
    assert policy.decide(*predictions(), [], SecurityContext()).route == Route.LOCAL
