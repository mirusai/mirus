"""Families expand during compilation, but execute only selected combinations."""

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def test_only_selected_combinations_execute_and_share_preparation():
    calls, field_calls = [], []

    @field(source="loans")
    def dollars(row) -> float:
        field_calls.append(row["amount"])
        return row["amount"] / 7

    @feature(
        source="loans",
        feature_name="loan_{loan_type}_{purpose}_amount_{window}",
        parameters={"loan_type": ["credit", "home", "private"],
                    "purpose": ["shopping", "medical"], "window": ["30d", "3m"]},
    )
    def amount(rows, *, loan_type, purpose, window) -> float:
        calls.append((loan_type, purpose, window))
        return sum((row["dollars"] for row in rows), 0.0)

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    names = ["loan_credit_shopping_amount_30d", "loan_home_medical_amount_3m", "count"]
    assert len(prepare_features().features_by_name) == 13
    assert tuple(prepare_features(names).features_by_name) == tuple(names)
    assert calls == field_calls == []  # Neither registration nor preparation computes.
    assert compute_features({"loans": [{"amount": 70}]}, names + [names[0]]) == {
        names[0]: 10.0, names[1]: 10.0, "count": 1,
    }
    assert calls == [("credit", "shopping", "30d"), ("home", "medical", "3m")]
    assert field_calls == [70]
    with pytest.raises(KeyError):
        compute_features({}, ["amount"])  # The Python function name is not an alias.


def test_bound_parameter_values_default_all_and_nullable_output():
    @feature(source="loans", feature_name="amount_{days}d", parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float | None:
        return float(days) if rows else None

    assert compute_features({"loans": [{}]}) == {"amount_30d": 30.0, "amount_90d": 90.0}
    assert compute_features({"loans": []}) == {"amount_30d": None, "amount_90d": None}
    assert amount([{}], days=7) == 7.0  # Original callable remains unchanged.


def test_empty_parameters_preserve_scalar_behavior():
    @feature(source="loans", parameters={})
    def count(rows) -> int:
        return len(rows)

    assert compute_features({"loans": [{}]}) == {"count": 1}


def test_decorator_snapshots_parameter_choices_before_compilation():
    days = [30]

    @feature(
        source="loans",
        feature_name="amount_{days}d",
        parameters={"days": days},
    )
    def amount(rows, *, days) -> float:
        return float(days)

    days.append(90)
    assert tuple(prepare_features().features_by_name) == ("amount_30d",)


def test_optional_function_parameters_keep_their_defaults():
    @feature(source="loans")
    def amount(rows, *, rate=2) -> float:
        return rate * len(rows)

    assert compute_features({"loans": [{}]}) == {"amount": 2}
