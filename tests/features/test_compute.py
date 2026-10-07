"""The customer-facing API uses decorators plus one function call."""

import importlib
import sys
from unittest.mock import patch

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features
from mirus.features.compiler import prepare_features

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


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
    with patch("mirus.features.compute.prepare_features", wraps=prepare_features) as prepare:
        assert compute_features(payload, ["count", "count"]) == {"count": 1}
    prepare.assert_called_once_with(["count", "count"])
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
    payload = {"loans": [{"amount": 70}]}
    assert compute_features(payload, ["total"]) == {"total": 70.0}
    assert calls == []

    @field(source="loans")
    def amount(row) -> float:
        calls.append("field")
        return row["amount"] / 7

    assert compute_features(payload, ["total"]) == {"total": 10.0}
    assert calls == ["field"]


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
    finally:
        sys.modules.pop(module_name, None)


def test_feature_names_must_not_be_a_bare_string():
    with pytest.raises(TypeError, match="sequence of names"):
        compute_features({}, "loan_count")
