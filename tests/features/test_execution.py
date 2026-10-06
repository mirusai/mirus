"""Selection must skip aggregations, not compute everything and filter results."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features
from mirus.backtest import compile_offline

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")


def test_selected_aggregations_share_all_fields_and_skip_other_sources():
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

    payload = {"loans": [{"amount": 70}, {"amount": 140}]}
    original = deepcopy(payload)
    assert calls == []  # Preparation does not run user code.
    assert compute_features(payload, ["count", "total_usd", "count"]) == {"count": 2, "total_usd": 30.0}
    assert calls == ["field", "unused_field", "field", "unused_field", "count", "total"]
    assert payload == original
    assert compile_offline(["count", "total_usd"]).output_schema == {
        "count": int,
        "total_usd": float,
    }


def test_single_feature_is_selected_by_published_name_and_runs_once():
    calls = []

    @feature(source="loans", feature_name="loan_count")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    assert compute_features({"loans": []}, ["loan_count", "loan_count"]) == {"loan_count": 0}
    assert calls == ["count"]
    assert compile_offline(["loan_count"]).output_schema == {"loan_count": int}
    with pytest.raises(KeyError):
        compute_features({}, ["count"])


def test_sources_are_independent_and_optional_objects_become_rows():
    @feature(source="loans")
    def count_loans(rows) -> int:
        return len(rows)

    @feature(source="report")
    def report_score(rows) -> float | None:
        return float(rows[0]["score"]) if rows else None

    assert compute_features({"loans": [], "report": {"score": 720}}) == {
        "count_loans": 0, "report_score": 720.0,
    }
    assert compute_features({"report": []}, ["report_score"]) == {
        "report_score": None
    }
    assert compute_features({"report": None}, ["report_score"]) == {
        "report_score": None
    }
    assert compute_features({}, ["report_score"]) == {"report_score": None}
    assert compute_features({}, []) == {}


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
    assert compute_features(payload) == {"total": 80, "count": 2, "device_user": "root"}
    assert field_calls == ["root", "row"]
    assert prepared_rows[0] is prepared_rows[1]
    assert prepared_rows[0] == [
        {"user_id": "root", "rate": 2, "active": False, "optional": None,
         "amount": 10, "adjusted_amount": 20},
        {"user_id": "row", "rate": 3, "active": False, "optional": None,
         "amount": 20, "adjusted_amount": 60},
    ]
    assert payload == original


def test_each_segment_receives_root_context_from_the_deepest_segment():
    seen = []

    @field(source="loans")
    def amount_usd(row) -> float:
        return row["amount"] / row["local_per_usd"]

    @feature(source="loans")
    def total(rows) -> float:
        seen.extend(rows)
        return sum(row["amount_usd"] for row in rows)

    agreement = {"agreement_id": "A1", "interest_rate": 0.08, "term": {"months": 12}}
    payload = {
        "user_id": "u1",
        "as_of": "2026-10-06",
        "local_per_usd": 7,
        "devices": [{"device_id": "d1"}],
        "loans": [{
            "loan_id": "l1",
            "amount": 700,
            "created_at": "2026-09-26",
            "agreement": agreement,
            "payments": [
                {"payment_id": "p1", "fee": {"fee_amount": 3}},
                {"payment_id": "p2"},
            ],
        }],
    }
    original = deepcopy(payload)
    loan = {
        "user_id": "u1",
        "as_of": "2026-10-06",
        "local_per_usd": 7,
        "loan_id": "l1",
        "amount": 700,
        "created_at": "2026-09-26",
        "amount_usd": 100.0,
        "agreement_id": "A1",
        "interest_rate": 0.08,
        "months": 12,
    }
    assert compute_features(payload, ["total"]) == {"total": 200.0}
    assert {"loans": seen} == {"loans": [
        {**loan, "payment_id": "p1", "fee_amount": 3},
        {**loan, "payment_id": "p2"},
    ]}
    assert payload == original
    assert payload["loans"][0]["agreement"] is agreement
    assert "device_id" not in seen[0]
    assert "agreement" not in seen[0]
    assert "term" not in seen[0]


@pytest.mark.parametrize("payload", [
    {"user_id": "root", "loans": []},
    {"user_id": "root", "loans": None},
    {"user_id": "root"},
    {"user_id": "root", "loans": ["not-a-record"]},
])
def test_root_context_does_not_create_rows_for_empty_sources(payload):
    @field(source="loans")
    def unused(row):
        raise AssertionError("Empty source must not run fields")

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert compute_features(payload) == {"count": 0}


def test_flatten_keeps_parent_rows_and_overrides_deeper_keys():
    seen = []

    @feature(source="loans")
    def count(rows) -> int:
        seen.extend(rows)
        return len(rows)

    tags = ["home", "open"]
    payload = {
        "user_id": "u1",
        "loans": [
            {
                "loan_id": "l1",
                "amount": 700,
                "tags": tags,
                "agreement": None,
                "payments": [
                    {"payment_id": "p1", "amount": 5, "parts": [{"part": 1}, {"part": 2}]},
                    {"payment_id": "p2", "amount": 9},
                ],
            },
            {"loan_id": "l2", "amount": 50, "payments": []},
        ],
    }
    original = deepcopy(payload)
    assert compute_features(payload, ["count"]) == {"count": 4}
    assert seen == [
        {
            "user_id": "u1", "loan_id": "l1", "amount": 700, "tags": ["home", "open"],
            "agreement": None, "payment_id": "p1", "part": 1,
        },
        {
            "user_id": "u1", "loan_id": "l1", "amount": 700, "tags": ["home", "open"],
            "agreement": None, "payment_id": "p1", "part": 2,
        },
        {
            "user_id": "u1", "loan_id": "l1", "amount": 700, "tags": ["home", "open"],
            "agreement": None, "payment_id": "p2",
        },
        {"user_id": "u1", "loan_id": "l2", "amount": 50, "payments": []},
    ]
    assert seen[0]["tags"] is not tags
    assert payload == original

    seen.clear()
    assert compute_features({"loans": [{
        "loan_id": "l3",
        "payments": [{"payment_id": "p"}],
        "fees": [{"fee": 1}, {"fee": 2}],
    }]}, ["count"]) == {"count": 2}
    assert seen == [
        {"loan_id": "l3", "payment_id": "p", "fee": 1},
        {"loan_id": "l3", "payment_id": "p", "fee": 2},
    ]


def test_empty_object_source_is_one_row():
    calls = []

    @field(source="report")
    def marker(row) -> int:
        calls.append("field")
        return 1

    @feature(source="report")
    def count(rows) -> int:
        return len(rows)

    assert compute_features({"user_id": "u1", "report": {}}) == {"count": 1}
    assert calls == ["field"]


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
        compute_features({})
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
        compute_features({})
    with pytest.raises(ValueError, match="Duplicate field"):
        compile_offline()


@pytest.mark.parametrize("returned", [None, 0, False])
def test_single_feature_preserves_empty_values(returned):
    @feature(source="loans")
    def value(rows):
        return returned

    assert compute_features({"loans": []}, ["value"])["value"] is returned


def test_unknown_selection_and_missing_type_fail_before_offline_use():
    with pytest.raises(KeyError):
        compute_features({}, ["missing"])

    @feature(source="loans")
    def untyped(rows):
        return len(rows)

    assert compute_features({"loans": []}, ["untyped"]) == {
        "untyped": 0
    }
    with pytest.raises(TypeError, match="return annotation"):
        compile_offline(["untyped"]).output_schema


def test_example_definitions_and_nullable_schema():
    path = Path(__file__).resolve().parents[2] / "examples" / "loan_features.py"
    spec = importlib.util.spec_from_file_location("loan_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert compute_features({"loans": []}) == {
        "loan_count": 0,
        "loan_total_amount_usd": 0.0,
        "largest_loan_amount_usd": None,
    }
    assert compile_offline().output_schema["largest_loan_amount_usd"] == float | None
