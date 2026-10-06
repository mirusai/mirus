"""Selection must skip aggregations, not compute everything and filter results."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compile_features
from mirus.backtest import compile_offline, feature_schema

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def test_selected_aggregations_share_all_fields_and_skip_other_sources(isolated_feature_registry):
    calls = []

    @field(source="loans")
    def dollar_amount(row) -> float:
        calls.append("field")
        return row["amount"] / 7

    @field(source="loans")
    def unused_field(row) -> int:
        calls.append("unused_field")
        return 42

    @feature(source="loans")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    @feature(source="loans", feature_name="total_usd")
    def total(rows) -> float:
        calls.append("total")
        return sum((row["dollar_amount"] for row in rows), 0.0)

    @feature(source="loans")
    def unselected(rows) -> float:
        raise AssertionError("Unselected aggregation ran")

    @field(source="devices")
    def device_field(row) -> int:
        raise AssertionError("Unselected source was prepared")

    @feature(source="devices")
    def device_count(rows) -> int:
        raise AssertionError("Unselected source aggregation ran")

    selected = compile_features(["count", "total_usd", "count"])
    payload = {"loans": [{"amount": 70}, {"amount": 140}]}
    original = deepcopy(payload)
    assert calls == []  # Selection does not run user code.
    assert selected(payload) == {"count": 2, "total_usd": 30.0}
    assert calls == ["field", "unused_field", "field", "unused_field", "count", "total"]
    assert payload == original
    assert feature_schema(["count", "total_usd"]) == {
        "count": int,
        "total_usd": float,
    }

    # The compiled plan owns its callables, independent of the registry.
    isolated_feature_registry._features.clear()
    assert selected({"loans": []}) == {"count": 0, "total_usd": 0.0}


def test_single_feature_is_selected_by_published_name_and_runs_once():
    calls = []

    @feature(source="loans", feature_name="loan_count")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    selected = compile_features(["loan_count", "loan_count"])
    assert selected({"loans": []}) == {"loan_count": 0}
    assert calls == ["count"]
    assert feature_schema(["loan_count"]) == {"loan_count": int}
    with pytest.raises(KeyError):
        compile_features(["count"])


def test_sources_are_independent_and_optional_objects_become_rows():
    @feature(source="loans")
    def count_loans(rows) -> int:
        return len(rows)

    @feature(source="report")
    def report_score(rows) -> float | None:
        return float(rows[0]["score"]) if rows else None

    assert compile_features()({"loans": [], "report": {"score": 720}}) == {
        "count_loans": 0, "report_score": 720.0,
    }
    assert compile_features(["report_score"])({"report": None}) == {
        "report_score": None
    }
    assert compile_features([])({}) == {}


def test_root_context_is_shared_with_fields_and_features_once_per_source():
    field_calls, prepared_rows = [], []

    @field(source="loans")
    def adjusted_amount(row) -> float:
        field_calls.append(row["user_id"])
        return row["amount"] * row["rate"]

    @feature(source="loans")
    def total(rows) -> float:
        prepared_rows.append(rows)
        return sum(row["adjusted_amount"] for row in rows)

    @feature(source="loans")
    def count(rows) -> int:
        prepared_rows.append(rows)
        return len(rows)

    @feature(source="device")
    def device_user(rows) -> str:
        assert rows == [{"user_id": "root", "rate": 2, "active": False,
                         "optional": None, "adjusted_amount": -1, "id": "phone"}]
        return rows[0]["user_id"]

    payload = {
        "user_id": "root", "rate": 2, "active": False, "optional": None,
        "adjusted_amount": -1,
        "loans": [{"amount": 10}, {"amount": 20, "user_id": "row", "rate": 3}],
        "device": {"id": "phone"}, "metadata": {"ignore": True}, "events": [1, 2],
    }
    original = deepcopy(payload)
    assert compile_features()(payload) == {"total": 80, "count": 2, "device_user": "root"}
    assert field_calls == ["root", "row"]
    assert prepared_rows[0] is prepared_rows[1]
    assert prepared_rows[0] == [
        {"user_id": "root", "rate": 2, "active": False, "optional": None,
         "amount": 10, "adjusted_amount": 20},
        {"user_id": "row", "rate": 3, "active": False, "optional": None,
         "amount": 20, "adjusted_amount": 60},
    ]
    assert payload == original


@pytest.mark.parametrize("source_rows", [[], None])
def test_root_context_does_not_create_rows_for_empty_sources(source_rows):
    @field(source="loans")
    def unused(row):
        raise AssertionError("Empty source must not run fields")

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert compile_features()({"user_id": "root", "loans": source_rows}) == {"count": 0}


def test_decorators_leave_functions_unchanged():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert count([{}]) == 1
    assert count.__name__ == "count"
    assert vars(count) == {}  # No wrappers or discovery metadata attached.


def test_duplicate_outputs_fail_during_online_and_offline_compilation():
    @feature(source="loans", feature_name="count")
    def count_loans(rows) -> int:
        return len(rows)

    @feature(source="devices", feature_name="count")
    def device_count(rows) -> int:
        return len(rows)

    with pytest.raises(ValueError, match="Duplicate feature: count"):
        compile_features()
    with pytest.raises(ValueError, match="Duplicate feature: count"):
        compile_offline()


@pytest.mark.parametrize("output_types", [{}, {"count": int}])
def test_dictionary_group_declaration_is_no_longer_supported(output_types):
    with pytest.raises(TypeError, match="output_types"):
        feature(source="loans", output_types=output_types)


def test_duplicate_fields_fail_during_compilation(isolated_feature_registry):
    @field(source="loans")
    def amount(row) -> float:
        return float(row["amount"])

    @field(source="loans")
    def amount(row) -> float:
        return 0.0

    assert len(isolated_feature_registry.snapshot().fields) == 2
    with pytest.raises(ValueError, match="Duplicate field"):
        compile_features()
    with pytest.raises(ValueError, match="Duplicate field"):
        compile_offline()


@pytest.mark.parametrize("returned", [None, 0, False])
def test_single_feature_preserves_empty_values(returned):
    @feature(source="loans")
    def value(rows):
        return returned

    assert compile_features(["value"])({"loans": []})["value"] is returned


def test_unknown_selection_and_missing_type_fail_before_offline_use():
    with pytest.raises(KeyError):
        compile_features(["missing"])

    @feature(source="loans")
    def untyped(rows):
        return len(rows)

    assert compile_features(["untyped"])({"loans": []}) == {
        "untyped": 0
    }
    with pytest.raises(TypeError, match="return annotation"):
        feature_schema(["untyped"])


def test_example_definitions_and_nullable_schema():
    path = Path(__file__).resolve().parents[2] / "examples" / "loan_features.py"
    spec = importlib.util.spec_from_file_location("loan_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = compile_features()
    assert selected({"loans": []}) == {
        "loan_count": 0,
        "loan_total_amount_usd": 0.0,
        "largest_loan_amount_usd": None,
    }
    assert feature_schema()["largest_loan_amount_usd"] == float | None
