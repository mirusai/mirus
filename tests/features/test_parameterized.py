"""Families expand during compilation, but execute only selected combinations."""

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features
from mirus.backtest import compile_offline

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
    assert len(compile_offline().output_schema) == 13
    assert compile_offline(names).output_schema == {names[0]: float, names[1]: float, "count": int}
    assert calls == field_calls == []  # Neither registration nor schema inspection computes.
    assert compute_features({"loans": [{"amount": 70}]}, names + [names[0]]) == {
        names[0]: 10.0, names[1]: 10.0, "count": 1,
    }
    assert calls == [("credit", "shopping", "30d"), ("home", "medical", "3m")]
    assert field_calls == [70]
    with pytest.raises(KeyError):
        compute_features({}, ["amount"])  # The Python function name is not an alias.


def test_bound_parameter_values_default_all_and_nullable_schema():
    @feature(source="loans", feature_name="amount_{days}d", parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float | None:
        return float(days) if rows else None

    assert compile_offline().output_schema == {"amount_30d": float | None, "amount_90d": float | None}
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
    assert compile_offline().output_schema == {"amount_30d": float}


def test_conflicting_family_fails_each_compile_without_changing_old_plan():
    @feature(source="loans", feature_name="amount_90d")
    def existing(rows) -> float:
        return 999.0

    catalog = prepare_features()

    @feature(source="loans", feature_name="amount_{days}d", parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="Duplicate feature: amount_90d"):
        compute_features({"loans": []})
    with pytest.raises(ValueError, match="Duplicate feature: amount_90d"):
        compile_offline()
    assert compute_features({"loans": []}, catalog=catalog) == {"amount_90d": 999.0}


def test_template_must_produce_unique_names():
    @feature(source="loans", feature_name="amount", parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="Duplicate feature"):
        compute_features({})
    with pytest.raises(ValueError, match="Duplicate feature"):
        compile_offline()


@pytest.mark.parametrize("choices", [[], "30d", 30])
def test_parameter_choices_must_be_nonempty_sequences(choices):
    @feature(source="loans", feature_name="amount_{days}", parameters={"days": choices})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="non-empty sequence"):
        compute_features({})
    with pytest.raises(ValueError, match="non-empty sequence"):
        compile_offline()


def test_parameterized_features_require_a_name_template():
    @feature(source="loans", parameters={"days": [30]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="feature_name template"):
        compute_features({})
    with pytest.raises(ValueError, match="feature_name template"):
        compile_offline()


def test_missing_template_parameter_fails_compilation():
    @feature(source="loans", feature_name="amount_{window}", parameters={"days": [30]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(KeyError):
        compute_features({})
    with pytest.raises(KeyError):
        compile_offline()


def test_parameter_names_must_bind_to_the_function():
    @feature(
        source="loans",
        feature_name="amount_{days}",
        parameters={"days": [30]},
    )
    def amount(rows, *, window) -> float:
        return float(window)

    with pytest.raises(TypeError, match="cannot bind declared parameters"):
        compute_features({})
    with pytest.raises(TypeError, match="cannot bind declared parameters"):
        compile_offline()
