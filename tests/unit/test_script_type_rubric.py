"""Rubric invariants: weights, uniqueness, immutability of history."""

from __future__ import annotations

from typing import Any

import pytest

from meobot.core.errors import ValidationError
from meobot.domain.script_types.rubric import (
    TOTAL_WEIGHT,
    ReviewRubric,
    validate_rubric_payload,
)


def rubric_payload(**overrides: Any) -> dict[str, Any]:
    """A valid rubric, optionally patched for the case under test."""
    payload: dict[str, Any] = {
        "passing_score": 70,
        "criteria": [
            {"code": "hook_strength", "name": "Sức hút mở đầu", "weight": 40},
            {"code": "clarity", "name": "Rõ ràng", "weight": 35},
            {"code": "call_to_action", "name": "Kêu gọi hành động", "weight": 25},
        ],
    }
    payload.update(overrides)
    return payload


def test_valid_rubric_sums_to_one_hundred() -> None:
    rubric = validate_rubric_payload(rubric_payload())
    assert rubric.total_weight == TOTAL_WEIGHT
    assert len(rubric.criteria) == 3


@pytest.mark.parametrize("weights", [(40, 35, 20), (50, 40, 20), (0, 0, 0)])
def test_weights_not_summing_to_one_hundred_are_rejected(weights: tuple[int, int, int]) -> None:
    criteria = [
        {"code": "hook_strength", "name": "A", "weight": weights[0]},
        {"code": "clarity", "name": "B", "weight": weights[1]},
        {"code": "call_to_action", "name": "C", "weight": weights[2]},
    ]
    with pytest.raises(ValidationError, match="sum to 100"):
        validate_rubric_payload(rubric_payload(criteria=criteria))


def test_duplicate_criterion_codes_are_rejected() -> None:
    criteria = [
        {"code": "clarity", "name": "A", "weight": 50},
        {"code": "clarity", "name": "B", "weight": 50},
    ]
    with pytest.raises(ValidationError, match="Duplicate criterion codes"):
        validate_rubric_payload(rubric_payload(criteria=criteria))


def test_negative_weight_is_rejected() -> None:
    criteria = [
        {"code": "clarity", "name": "A", "weight": -10},
        {"code": "hook_strength", "name": "B", "weight": 110},
    ]
    with pytest.raises(ValidationError):
        validate_rubric_payload(rubric_payload(criteria=criteria))


def test_empty_rubric_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_rubric_payload(rubric_payload(criteria=[]))


def test_unknown_field_is_rejected() -> None:
    """extra='forbid' stops a typo from silently becoming a no-op."""
    with pytest.raises(ValidationError):
        validate_rubric_payload(rubric_payload(weigths=100))


def test_rubric_is_immutable() -> None:
    """A stored rubric must never be edited in place - versions are appended."""
    rubric = validate_rubric_payload(rubric_payload())
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError on frozen set
        rubric.passing_score = 90  # type: ignore[misc]


def test_criterion_lookup() -> None:
    rubric: ReviewRubric = validate_rubric_payload(rubric_payload())
    assert rubric.criterion("clarity").weight == 35
    with pytest.raises(ValidationError):
        rubric.criterion("does_not_exist")


def test_seeded_rubrics_are_valid() -> None:
    """Every rubric shipped in migration 0002 must satisfy the invariants.

    The migration is loaded from disk rather than imported: ``alembic/versions``
    is not a package, and this test must fail if the seed data ever drifts.
    """
    import importlib.util
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    path = repo_root / "alembic" / "versions" / "0002_seed_script_types.py"
    spec = importlib.util.spec_from_file_location("meobot_seed_0002", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for item in module.SEED_DATA:
        rubric = validate_rubric_payload(item["rubric"])
        assert rubric.total_weight == TOTAL_WEIGHT, item["code"]
