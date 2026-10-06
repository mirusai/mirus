"""Catalog preparation is optional, reusable, and independent of request selection."""

import pickle
from unittest.mock import patch

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def _amount(row):
    return row["amount"]


def _total(rows):
    return sum(row["_amount"] for row in rows)


def test_catalog_accepts_different_subsets_without_recompilation():
    calls = []

    @field(source="loans")
    def amount(row):
        calls.append("field")
        return row["amount"]

    @feature(source="loans", feature_name="total_{days}", parameters={"days": [30, 90]})
    def total(rows, *, days):
        calls.append(days)
        return sum(row["amount"] for row in rows) + days

    @feature(source="unused")
    def unused(rows):
        raise AssertionError("Unselected source must not execute")

    catalog = prepare_features()
    assert calls == []
    with patch("mirus.features.compute.prepare_features", side_effect=AssertionError("recompiled")):
        payload = {"loans": [{"amount": 5}]}
        assert compute_features(payload, ["total_30"], catalog=catalog) == {"total_30": 35}
        assert compute_features(payload, ["total_90", "total_90"], catalog=catalog) == {"total_90": 95}
        assert compute_features({}, [], catalog=catalog) == {}
    assert calls == ["field", 30, "field", 90]


def test_restricted_catalog_defaults_to_its_own_features_and_rejects_others():
    @feature(source="loans", feature_name="value_{n}", parameters={"n": [1, 2, 3]})
    def value(rows, *, n):
        return n

    catalog = prepare_features(["value_2", "value_1", "value_2"])
    assert catalog.feature_names == ("value_2", "value_1")
    assert compute_features({"loans": []}, catalog=catalog) == {"value_2": 2, "value_1": 1}
    with pytest.raises(KeyError, match="value_3"):
        compute_features({}, ["value_3"], catalog=catalog)
    with pytest.raises(TypeError, match="sequence"):
        compute_features({}, "value_2", catalog=catalog)


def test_restricted_preparation_binds_only_selected_combinations():
    from functools import partial

    @feature(source="loans", feature_name="value_{n}", parameters={"n": range(12000)})
    def value(rows, *, n):
        return n

    with patch("mirus.features.compiler.partial", wraps=partial) as bind:
        catalog = prepare_features(["value_1", "value_11999"])
    assert bind.call_count == 2
    assert catalog.feature_names == ("value_1", "value_11999")


def test_prepared_catalog_survives_registry_changes(isolated_feature_registry):
    @feature(source="loans")
    def count(rows):
        return len(rows)

    catalog = prepare_features()
    isolated_feature_registry._features.clear()
    assert compute_features({"loans": [{}, {}]}, catalog=catalog) == {"count": 2}
    assert compute_features({}) == {}


def test_empty_catalog_does_not_fall_back_to_registered_features():
    @feature(source="loans")
    def count(rows):
        raise AssertionError("Empty catalog must stay empty")

    catalog = prepare_features([])
    assert catalog.fields_by_source == {}
    assert compute_features({}, catalog=catalog) == {}


def test_catalog_pickle_roundtrip_for_importable_user_functions(isolated_feature_registry):
    field(source="loans")(_amount)
    feature(source="loans", feature_name="total")(_total)
    catalog = pickle.loads(pickle.dumps(prepare_features()))
    isolated_feature_registry._features.clear()
    isolated_feature_registry._fields.clear()
    assert compute_features({"loans": [{"amount": 4}]}, catalog=catalog) == {"total": 4}
