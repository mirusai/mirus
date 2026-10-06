"""Online and offline compilers must resolve the same raw declarations."""

from functools import partial

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features
from mirus.backtest import compile_offline

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def _base_function(function):
    return function.func if isinstance(function, partial) else function


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
    assert len(isolated_feature_registry.snapshot().features) == 1
    names = ["amount_90d", "amount_30d", "amount_90d"]
    offline = compile_offline(names)

    assert offline.feature_names == ("amount_90d", "amount_30d")
    assert offline.output_schema == {
        "amount_90d": float,
        "amount_30d": float,
    }
    assert offline.required_sources == ("loans",)
    assert offline.field_names_by_source == {"loans": ("dollars",)}
    assert offline.fields_by_source["loans"][0].function is dollars

    online_features = tuple(prepare_features(names).features_by_name.values())
    offline_features = offline.features_by_source["loans"]
    assert tuple(feature.name for feature in online_features) == offline.feature_names
    assert tuple(feature.name for feature in online_features) == tuple(
        feature.name for feature in offline_features
    )
    assert tuple(_base_function(feature.function) for feature in online_features) == (
        amount,
        amount,
    )
    assert tuple(_base_function(feature.function) for feature in offline_features) == (
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
