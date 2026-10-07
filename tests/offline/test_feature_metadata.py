"""Online and offline compilers must resolve the same raw declarations."""

from functools import partial

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features
from mirus.backtest import compile_offline

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def _base_function(function):
    return function.func if isinstance(function, partial) else function


def test_single_feature_metadata_does_not_execute_or_require_unused_annotations():
    @field(source="loans")
    def amount(row):
        raise AssertionError("Metadata executed a field")

    @feature(source="loans", feature_name="loan_count")
    def count(rows) -> int:
        raise AssertionError("Metadata executed a feature")

    @feature(source="devices")
    def unused(rows):
        raise AssertionError("Metadata executed an unselected feature")

    plan = compile_offline(["loan_count"])
    assert plan.output_types == (("loan_count", int),)
    assert plan.output_schema == {"loan_count": int}
    assert compile_offline([]).output_types == ()


def test_parameterized_metadata_preserves_selection_and_nullable_types():
    @feature(source="loans", feature_name="amount_{kind}_{purpose}_{days}",
             parameters={"kind": ["credit", "home", "private"],
                         "purpose": ["shopping", "medical"], "days": [30, 90]})
    def amount(rows, *, kind, purpose, days) -> float | None:
        raise AssertionError("Metadata executed a feature")

    @feature(source="loans")
    def count(rows) -> int:
        raise AssertionError("Metadata executed a feature")

    assert len(compile_offline().output_schema) == 13
    names = ["amount_home_medical_90", "count", "amount_credit_shopping_30"]
    plan = compile_offline(names)
    assert plan.feature_names == tuple(names)
    assert plan.output_types == ((names[0], float | None), ("count", int), (names[2], float | None))
    assert plan.output_schema == {names[0]: float | None, "count": int, names[2]: float | None}


def test_output_types_requires_return_annotations_when_accessed():
    @feature(source="loans")
    def untyped(rows):
        return len(rows)

    plan = compile_offline(["untyped"])
    with pytest.raises(TypeError, match="return annotation"):
        _ = plan.output_types


def test_online_and_offline_compilers_have_selection_parity(
    isolated_feature_registry,
):
    @field(source="loans")
    def dollars(row) -> float:
        return row["amount"] / 7

    @feature(
        source="loans",
        feature_name="amount_{days}d",
        parameters={"days": [30, 90]},
    )
    def amount(rows, *, days) -> float:
        return sum((row["dollars"] for row in rows), 0.0) + days

    # Collection stores one raw family; each compiler expands it independently.
    _, declarations = isolated_feature_registry.snapshot()
    assert len(declarations) == 1
    names = ["amount_90d", "amount_30d", "amount_90d"]
    offline = compile_offline(names)

    assert offline.feature_names == ("amount_90d", "amount_30d")
    assert offline.output_schema == {
        "amount_90d": float,
        "amount_30d": float,
    }
    assert offline.required_sources == ("loans",)
    assert offline.field_names_by_source == {"loans": ("dollars",)}
    assert offline.fields_by_source["loans"]["dollars"] is dollars

    online = prepare_features(names)
    online_features = online.features_by_source["loans"]
    offline_features = offline.features_by_source["loans"]
    assert offline.features_by_source is offline.catalog.features_by_source
    assert tuple(online_features) == offline.feature_names == tuple(offline_features)
    assert tuple(_base_function(function) for function in online_features.values()) == (
        amount,
        amount,
    )
    assert tuple(_base_function(function) for function in offline_features.values()) == (
        amount,
        amount,
    )


def test_compilers_are_uncached_and_use_fresh_registry_snapshots():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    online_first = prepare_features()
    online_second = prepare_features()
    offline_first = compile_offline()
    offline_second = compile_offline()

    assert online_first is not online_second
    assert offline_first is not offline_second
    assert offline_first.feature_names == ("count",)

    @feature(source="loans")
    def twice_count(rows) -> int:
        return 2 * len(rows)

    assert compile_offline().feature_names == ("count", "twice_count")
    assert compute_features({"loans": [{}]}, catalog=online_first) == {"count": 1}
