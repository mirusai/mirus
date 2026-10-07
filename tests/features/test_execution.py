"""Selection must skip aggregations, not compute everything and filter results."""

from copy import deepcopy

import pytest

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features, prepare_features

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


def test_single_feature_is_selected_by_published_name_and_runs_once():
    calls = []

    @feature(source="loans", feature_name="loan_count")
    def count(rows) -> int:
        calls.append("count")
        return len(rows)

    assert compute_features({"loans": []}, ["loan_count", "loan_count"]) == {"loan_count": 0}
    assert calls == ["count"]
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


def test_nested_lists_are_reduced_or_transformed_without_expanding_loans():
    calls, seen = [], []

    @field(source="loans")
    def amount_usd(row) -> float:
        calls.append(row["loan_id"])
        return row["agreement"]["terms"]["amount"] / row["local_per_usd"]

    @field(source="loans")
    def payment_amounts(row) -> list[float]:
        return [payment["amount"] for payment in row["payments"]]

    @field(source="loans")
    def paid_amount(row) -> float:
        return sum(payment["amount"] for payment in row["payments"])

    @feature(source="loans")
    def total(rows) -> float:
        seen.extend(rows)
        return sum(row["amount_usd"] for row in rows)

    @feature(source="loans")
    def largest_payment(rows) -> float | None:
        return max((amount for row in rows for amount in row["payment_amounts"]), default=None)

    @feature(source="loans")
    def total_paid(rows) -> float:
        return sum(row["paid_amount"] for row in rows)

    payload = {
        "user_id": "u1", "local_per_usd": 7,
        "loans": [
            {"loan_id": "L1", "agreement": {"terms": {"amount": 700}},
             "payments": [{"amount": 100}, {"amount": 200}],
             "fees": [{"amount": 1}, {"amount": 2}, {"amount": 3}]},
            {"loan_id": "L2", "agreement": {"terms": {"amount": 350}}, "payments": []},
        ],
    }
    original = deepcopy(payload)
    assert compute_features(payload) == {"total": 150.0, "largest_payment": 200, "total_paid": 300}
    assert calls == ["L1", "L2"]
    assert len(seen) == 2
    assert seen[0]["user_id"] == "u1"
    assert seen[0]["payment_amounts"] == [100, 200]
    assert seen[1]["payment_amounts"] == []
    assert seen[0]["agreement"] is payload["loans"][0]["agreement"]
    assert seen[0]["payments"] is payload["loans"][0]["payments"]
    assert payload == original


@pytest.mark.parametrize("payload", [
    {"user_id": "root", "loans": []},
    {"user_id": "root", "loans": None},
    {"user_id": "root"},
])
def test_root_context_does_not_create_rows_for_empty_sources(payload):
    @field(source="loans")
    def unused(row):
        raise AssertionError("Empty source must not run fields")

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    assert compute_features(payload) == {"count": 0}


def test_malformed_records_are_not_silently_treated_as_empty():
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    with pytest.raises(TypeError):
        compute_features({"loans": ["not-a-record"]})


def test_nested_names_remain_independent_of_dictionary_order():
    @feature(source="loans")
    def ids(rows):
        return [(row["id"], row["agreement"]["id"], row["device"]["id"]) for row in rows]

    loan = {"id": "L1", "agreement": {"id": "A1"}, "device": {"id": "D1"}}
    for record in (loan, dict(reversed(list(loan.items())))):
        assert compute_features({"loans": [record]}) == {"ids": [("L1", "A1", "D1")]}


def test_optional_nested_objects_and_lists_preserve_source_records():
    @field(source="loans")
    def amount(row) -> float | None:
        return row["agreement"]["amount"] if row["agreement"] is not None else None

    @feature(source="loans")
    def values(rows):
        return [(row["amount"], row["payments"]) for row in rows]

    assert compute_features({"loans": [{"agreement": None, "payments": []}]}) == {"values": [(None, [])]}


def test_fields_read_original_values_without_field_dependencies():
    @field(source="loans")
    def amount(row):
        return row["amount"] * 2

    @field(source="loans")
    def raw_amount(row):
        return row["amount"]

    @feature(source="loans")
    def values(rows):
        return [(row["amount"], row["raw_amount"]) for row in rows]

    assert compute_features({"loans": [{"amount": 5}]}) == {"values": [(10, 5)]}


def test_result_order_matches_selection_across_sources():
    @feature(source="loans")
    def first(rows):
        return 1

    @feature(source="devices")
    def second(rows):
        return 2

    @feature(source="loans")
    def third(rows):
        return 3

    names = ["third", "second", "first"]
    assert list(compute_features({}, names)) == names
    assert list(compute_features({})) == ["first", "second", "third"]
    catalog = prepare_features([*names, "third"])
    assert list(catalog.features_by_name) == names
    assert list(compute_features({}, catalog=catalog)) == names


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


@pytest.mark.parametrize("returned", [None, 0, False])
def test_single_feature_preserves_empty_values(returned):
    @feature(source="loans")
    def value(rows):
        return returned

    assert compute_features({"loans": []}, ["value"])["value"] is returned


def test_online_features_do_not_require_return_annotations():
    @feature(source="loans")
    def untyped(rows):
        return len(rows)

    assert compute_features({"loans": []}, ["untyped"]) == {
        "untyped": 0
    }
