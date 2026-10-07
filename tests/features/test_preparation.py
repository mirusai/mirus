"""Catalog preparation fixes the selection and is optional and reusable."""

import pickle
from dataclasses import replace
from unittest.mock import patch

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def _amount(row):
    return row["amount"]


def _total(rows, *, days=0):
    return sum(row["_amount"] for row in rows) + days


@pytest.mark.parametrize("requested_names", [None, ["total_90"], ["missing"], [], "total_90"])
def test_catalog_takes_precedence_without_recompilation(requested_names):
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

    catalog = prepare_features(["total_30"])
    assert calls == []
    with patch("mirus.features.compute.prepare_features", side_effect=AssertionError("recompiled")):
        payload = {"loans": [{"amount": 5}]}
        assert compute_features(payload, requested_names, catalog=catalog) == {"total_30": 35}
        assert compute_features({}, requested_names, catalog=catalog) == {"total_30": 30}
    assert calls == ["field", 30, 30]


def test_restricted_catalog_contains_only_prepared_outputs():
    @feature(source="loans", feature_name="value_{n}", parameters={"n": [1, 2, 3]})
    def value(rows, *, n):
        return n

    catalog = prepare_features(["value_2", "value_1", "value_2"])
    assert tuple(catalog.features_by_name) == ("value_2", "value_1")
    assert compute_features({"loans": []}, catalog=catalog) == {"value_2": 2, "value_1": 1}


def test_restricted_preparation_binds_only_selected_combinations():
    from functools import partial

    @feature(source="loans", feature_name="value_{n}", parameters={"n": range(12000)})
    def value(rows, *, n):
        return n

    with patch("mirus.features.compiler.partial", wraps=partial) as bind:
        catalog = prepare_features(["value_1", "value_11999"])
    assert bind.call_count == 2
    assert tuple(catalog.features_by_name) == ("value_1", "value_11999")
    assert compute_features({}, catalog=catalog) == {"value_1": 1, "value_11999": 11999}


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
    assert compute_features({}, ["count"], catalog=catalog) == {}


@pytest.mark.parametrize("parameters, name, expected", [
    (None, "total", {"total": 4}),
    ({"days": [30, 90]}, "total_{days}", {"total_30": 34, "total_90": 94}),
])
def test_catalog_pickle_roundtrip_for_importable_user_functions(
    isolated_feature_registry, parameters, name, expected,
):
    field(source="loans")(_amount)
    feature(source="loans", feature_name=name, parameters=parameters)(_total)
    catalog = pickle.loads(pickle.dumps(prepare_features()))
    isolated_feature_registry._features.clear()
    isolated_feature_registry._fields.clear()
    assert compute_features({"loans": [{"amount": 4}]}, catalog=catalog) == expected


def test_registry_snapshot_copies_both_declaration_lists(isolated_feature_registry):
    field(source="loans")(_amount)
    feature(source="loans")(_total)
    fields, features = isolated_feature_registry.snapshot()
    isolated_feature_registry._fields.clear()
    isolated_feature_registry._features.clear()
    assert isinstance(fields, tuple) and isinstance(features, tuple)
    assert len(fields) == len(features) == 1
    assert fields[0].function is _amount
    assert features[0].function is _total


@pytest.mark.parametrize("names, sources", [
    (None, ("loans", "devices")),
    (["total"], ("loans",)),
    (["device_count"], ("devices",)),
    ([], ()),
])
def test_preparation_attaches_fields_only_to_selected_sources(names, sources):
    field(source="loans")(_amount)
    field(source="unused")(_amount)

    @field(source="loans")
    def fee(row):
        return row["fee"]

    feature(source="loans", feature_name="total")(_total)
    feature(source="devices", feature_name="device_count")(len)

    catalog = prepare_features(names)
    assert tuple(catalog.fields_by_source) == sources
    assert tuple(catalog.features_by_source) == sources
    if "loans" in sources:
        assert tuple(catalog.fields_by_source["loans"].values()) == (_amount, fee)
    if "devices" in sources:
        assert catalog.fields_by_source["devices"] == {}


def test_preparation_does_not_validate_callable_signatures():
    @field(source="loans")
    def amount(row):
        return row["amount"]

    @feature(source="loans")
    def total(rows) -> float:
        return sum(row["amount"] for row in rows)

    with patch("inspect.signature", side_effect=AssertionError("Runtime signature validation")):
        catalog = prepare_features()
        assert compute_features({"loans": [{"amount": 3}]}, catalog=catalog) == {"total": 3}


def test_catalog_precompiles_groups_and_reuses_feature_references():
    @feature(source="loans")
    def first(rows) -> int:
        return len(rows)

    @feature(source="devices")
    def second(rows) -> int:
        return len(rows)

    @feature(source="loans")
    def third(rows) -> int:
        return 3

    catalog = prepare_features()
    assert tuple(catalog.features_by_source) == ("loans", "devices")
    assert catalog.features_by_source["loans"] == {"first": first, "third": third}
    assert catalog.features_by_name["first"] == ("loans", first)
    assert catalog.features_by_source["loans"]["first"] is catalog.features_by_name["first"][1]


def test_prepared_computation_does_not_lookup_or_regroup_features():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    class NoLookup(dict):
        def __getitem__(self, name):
            raise AssertionError("Precompiled execution looked up a feature")

    catalog = prepare_features()
    catalog = replace(catalog, features_by_name=NoLookup(catalog.features_by_name))
    with (
        patch("mirus.features.compute.prepare_features", side_effect=AssertionError("recompiled")),
        patch("mirus.features.compiler._group_by_source", side_effect=AssertionError("regrouped")),
    ):
        assert compute_features({"loans": [{}, {}]}, catalog=catalog) == {"count": 2}


@pytest.mark.parametrize("prepared", [False, True])
def test_unknown_name_fails_before_user_functions_execute(prepared):
    @field(source="loans")
    def amount(row):
        raise AssertionError("Field executed before resolving all selected names")

    @feature(source="loans")
    def total(rows) -> float:
        raise AssertionError("Feature executed before resolving all selected names")

    with pytest.raises(KeyError, match="missing"):
        if prepared:
            prepare_features(["total", "missing"])
        else:
            compute_features({"loans": [{}]}, ["total", "missing"])
