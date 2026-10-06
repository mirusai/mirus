"""The customer-facing API uses decorators plus one function call."""

import importlib
import sys

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features
from mirus.backtest import OfflinePlan, compile_offline

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def test_public_api_exports_come_from_isolated_modules():
    from mirus import backtest
    from mirus.features import compute, compiler, decorators

    assert compute_features is compute.compute_features
    assert prepare_features is compiler.prepare_features
    assert compile_offline is backtest.compile_offline
    assert OfflinePlan is backtest.OfflinePlan
    assert feature is decorators.feature
    assert field is decorators.field


def test_selected_computation_still_validates_unselected_declarations():
    @feature(source="loans")
    def valid(rows):
        return len(rows)  # Online computation still allows unannotated functions.

    @feature(source="loans", feature_name="invalid_{days}", parameters={"days": []})
    def invalid(rows, *, days):
        return days

    # Decoration remains permissive; even a subset validates the entire catalog.
    with pytest.raises(ValueError, match="non-empty sequence"):
        compute_features({"loans": []}, feature_names=["valid"])


def test_default_all_subset_and_empty_selection():
    calls = []

    @field(source="loans")
    def dollars(row) -> float:
        calls.append("field")
        return row["amount"] / 7

    @feature(source="loans")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    @feature(source="loans", feature_name="total_usd")
    def total(rows) -> float:
        calls.append("total")
        return sum((row["dollars"] for row in rows), 0.0)

    payload = {"loans": [{"amount": 70}]}
    assert compute_features(payload, ["count", "count"]) == {"count": 1}
    assert calls == ["field", "count"]
    calls.clear()
    assert compute_features(payload) == {"count": 1, "total_usd": 10.0}
    assert calls == ["field", "count", "total"]
    calls.clear()
    assert compute_features({}, []) == {}
    assert calls == []


def test_results_use_the_current_payload():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert compile_offline(["count"]).output_schema == {"count": int}
    assert compute_features({"loans": [{}]}, ["count"]) == {"count": 1}
    assert compute_features({"loans": [{}, {}]}, ["count"]) == {"count": 2}


def test_new_feature_appears_in_the_next_computation():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert compute_features({"loans": [{}]}) == {"count": 1}

    @feature(source="loans")
    def twice_count(rows) -> int:
        return 2 * len(rows)

    assert compute_features({"loans": [{}]}) == {"count": 1, "twice_count": 2}


def test_new_field_appears_in_the_next_computation():
    @feature(source="loans")
    def total(rows) -> float:
        return sum((row["amount"] for row in rows), 0.0)

    calls = []

    @field(source="loans")
    def dollars(row) -> float:
        calls.append("field")
        return row["amount"] / 7

    assert compute_features({"loans": [{"amount": 70}]}, ["total"]) == {"total": 70.0}
    assert calls == ["field"]


def test_single_feature_schema_does_not_execute_and_unused_source_is_not_read():
    calls = []

    @feature(source="loans")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    @feature(source="devices")
    def unused(rows) -> int:
        raise AssertionError("Unselected aggregation ran")

    assert compile_offline(["count"]).output_schema == {"count": int}
    assert calls == []
    assert compute_features({"loans": []}, ["count"]) == {"count": 0}
    assert calls == ["count"]
    with pytest.raises(KeyError):
        compute_features({"loans": []}, ["largest"])


def test_customer_module_registers_on_import_only(tmp_path, monkeypatch):
    # Simulate customer-owned code outside the installed mirus package.
    module_name = "customer_loan_features"
    (tmp_path / f"{module_name}.py").write_text(
        "from mirus.features.decorators import feature\n"
        "@feature(source='loans')\n"
        "def customer_count(rows) -> int:\n"
        "    return len(rows)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert compute_features({}) == {}  # No automatic filesystem discovery.
    try:
        module = importlib.import_module(module_name)
        assert compute_features({"loans": [{}]}) == {"customer_count": 1}
        assert importlib.import_module(module_name) is module
        assert compile_offline().output_schema == {"customer_count": int}
    finally:
        sys.modules.pop(module_name, None)


def test_duplicate_declarations_fail_during_each_prepare():
    @feature(source="loans", feature_name="count")
    def first(rows) -> int:
        return len(rows)

    catalog = prepare_features()

    @feature(source="devices", feature_name="count")
    def second(rows) -> int:
        return 999

    with pytest.raises(ValueError, match="Duplicate feature"):
        compute_features({"loans": [{}]})
    with pytest.raises(ValueError, match="Duplicate feature"):
        compile_offline()
    assert compute_features({"loans": [{}]}, catalog=catalog) == {"count": 1}


def test_feature_names_must_not_be_a_bare_string():
    with pytest.raises(TypeError, match="sequence of names"):
        compute_features({}, "loan_count")
