"""Opt-in Spark regressions for observation isolation and historical availability."""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from mirus.backtest import Backtest, compile_offline
from mirus.features.compute import compute_features
from mirus.features.decorators import feature, field
from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship
from mirus.serving import OnlineFetcher
from mirus.validation import validate_payload

pytestmark = pytest.mark.skipif(
    os.getenv("SPARK_INTEGRATION_TEST") != "1", reason="set SPARK_INTEGRATION_TEST=1"
)


def day(number):
    return datetime(2026, 1, number)


@pytest.fixture(scope="module")
def spark():
    session = (
        pytest.importorskip("pyspark.sql").SparkSession.builder.master("local[1]")
        .appName("mirus-pit-regressions")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture
def payload(spark):
    loans = PayloadSection(
        name="loans", db_table="loans", dwh_table="pit_loans",
        fields={
            "loan_id": Field("string", primary_key=True, dwh_column="loan_key"),
            "user_id": Field("string"), "agreement_id": Field("string"),
            "amount": Field("int32", dwh_column="principal"),
            "createdat": Field("timestamp", available_at=True),
        },
        relationship=Relationship("one-to-many", [JoinKey("user_id", "user_id")]),
    )
    spark.createDataFrame(
        [("L1", "U1", "A1", 100, day(1)), ("L2", "U1", "A1", 200, day(15))],
        "loan_key string, user_id string, agreement_id string, principal int, createdat timestamp",
    ).createOrReplaceTempView("pit_loans")
    return validate_payload(Payload("pit", 1, PayloadSection(
        name="payload", source="request",
        fields={"user_id": Field("string", primary_key=True),
                "as_of": Field("timestamp", observation_time=True)},
        children={"loans": loans},
    )))


def fetch_cells(spark, payload, observations):
    from mirus.backtest.spark.fetcher import SparkFetcher

    driver = spark.createDataFrame(observations, "user_id string, as_of timestamp").repartition(2)
    return {(row.user_id, row.as_of): row.payload.asDict(recursive=True)
            for row in SparkFetcher(payload, spark).fetch(driver).collect()}


def test_repeated_user_observations_exclude_future_data_at_boundaries(spark, payload):
    before_creation = datetime(2025, 12, 31)
    cells = fetch_cells(spark, payload, [
        ("U1", before_creation), ("U1", day(1)), ("U1", day(14)), ("U1", day(15)), ("U2", day(15)),
    ])
    assert cells["U1", before_creation]["loans"] == []
    assert [loan["loan_id"] for loan in cells["U1", day(1)]["loans"]] == ["L1"]
    assert [loan["loan_id"] for loan in cells["U1", day(14)]["loans"]] == ["L1"]
    assert [loan["loan_id"] for loan in cells["U1", day(15)]["loans"]] == ["L1", "L2"]
    assert cells["U2", day(15)]["loans"] == []


def test_immutable_source_does_not_build_a_version_window(spark, payload):
    from mirus.backtest.spark.temporal import read_history

    history = read_history(spark, payload.root.children["loans"])

    assert history.columns == [*payload.root.children["loans"].fields, "__mirus_from", "__mirus_to"]
    assert "Window" not in history._jdf.queryExecution().analyzed().toString()


@pytest.fixture
def history_payload(spark, payload):
    loans = payload.root.children["loans"]
    loans.dwh_table = "pit_history"
    loans.mutable = True
    loans.fields["createdat"].available_at = False
    loans.fields["updatedat"] = Field("timestamp", available_at=True, dwh_column="version_time")
    loans.field_mapping["updatedat"] = "version_time"
    spark.createDataFrame(
        [
            ("L1", "U1", "A1", 175, day(1), day(20)),
            ("L2", "U1", "A1", 200, day(15), day(15)),
            ("L1", "U1", "A1", 100, day(1), day(1)),
            ("L1", "U1", "A1", 140, day(1), day(10)),
        ],
        "loan_key string, user_id string, agreement_id string, principal int, createdat timestamp, "
        "version_time timestamp",
    ).repartition(2).createOrReplaceTempView("pit_history")
    return validate_payload(payload)


def test_complete_history_selects_latest_available_version_without_cdc_metadata(spark, history_payload):
    expectations = {1: [100], 9: [100], 10: [140], 15: [140, 200], 20: [175, 200], 28: [175, 200]}
    before_creation = datetime(2025, 12, 31)
    cells = fetch_cells(spark, history_payload, [
        ("U1", before_creation), ("U2", day(20)),
        *[("U1", day(number)) for number in expectations],
    ])
    for number, amounts in expectations.items():
        assert [loan["amount"] for loan in cells["U1", day(number)]["loans"]] == amounts
    assert cells["U1", before_creation]["loans"] == []
    assert cells["U2", day(20)]["loans"] == []
    assert cells["U1", day(20)]["loans"][0]["createdat"] == day(1)
    assert cells["U1", day(20)]["loans"][0]["updatedat"] == day(20)


def test_latest_version_is_selected_before_matching_mutable_relationship_keys(spark, history_payload):
    spark.createDataFrame([
        ("L1", "U1", "A1", 100, day(1), day(1)),
        ("L1", "U2", "A1", 140, day(1), day(10)),
    ], "loan_key string, user_id string, agreement_id string, principal int, createdat timestamp, "
       "version_time timestamp").createOrReplaceTempView("pit_history")

    cells = fetch_cells(spark, history_payload, [
        ("U1", day(9)), ("U2", day(9)), ("U1", day(10)), ("U2", day(10)),
    ])

    assert cells["U1", day(9)]["loans"][0]["amount"] == 100
    assert cells["U2", day(9)]["loans"] == []
    assert cells["U1", day(10)]["loans"] == []
    assert cells["U2", day(10)]["loans"][0]["amount"] == 140


def test_history_versions_are_partitioned_by_composite_primary_key(spark, history_payload):
    from mirus.backtest.spark.fetcher import SparkFetcher

    loans = history_payload.root.children["loans"]
    loans.fields["tenant_id"] = Field("string", primary_key=True)
    loans.relationship.keys.append(JoinKey("tenant_id", "tenant_id"))
    history_payload.root.fields["tenant_id"] = Field("string", primary_key=True)
    spark.createDataFrame([
        ("L1", "U1", "A1", 100, day(1), day(1), "T1"),
        ("L1", "U1", "A1", 150, day(1), day(10), "T1"),
        ("L1", "U1", "A1", 200, day(1), day(1), "T2"),
    ], "loan_key string, user_id string, agreement_id string, principal int, createdat timestamp, "
       "version_time timestamp, tenant_id string").createOrReplaceTempView("pit_history")
    driver = spark.createDataFrame([
        ("U1", day(9), "T1"), ("U1", day(10), "T1"), ("U1", day(10), "T2"),
    ], "user_id string, as_of timestamp, tenant_id string")

    result = SparkFetcher(history_payload, spark).fetch(driver)

    assert {(row.tenant_id, row.as_of.day): row.payload.loans[0].amount
            for row in result.collect()} == {("T1", 9): 100, ("T1", 10): 150, ("T2", 10): 200}


def test_nested_history_uses_request_time_for_each_parent(spark, history_payload):
    history_payload.root.children["loans"].children["agreement"] = PayloadSection(
        name="agreement", dwh_table="pit_agreement_history", mutable=True,
        fields={"agreement_id": Field("string", primary_key=True), "term": Field("int32"),
                "updatedat": Field("timestamp", available_at=True)},
        relationship=Relationship("many-to-one", [JoinKey("agreement_id", "agreement_id")]),
    )
    spark.createDataFrame([
        ("A1", 12, day(5)), ("A1", 18, day(12)),
    ], "agreement_id string, term int, updatedat timestamp").createOrReplaceTempView("pit_agreement_history")

    cells = fetch_cells(spark, history_payload, [("U1", day(1)), ("U1", day(10)), ("U1", day(15))])

    assert cells["U1", day(1)]["loans"][0]["agreement"] is None
    assert cells["U1", day(10)]["loans"][0]["agreement"]["term"] == 12
    assert [loan["agreement"]["term"] for loan in cells["U1", day(15)]["loans"]] == [18, 18]


@pytest.mark.usefixtures("isolated_feature_registry")
@pytest.mark.parametrize("backend", ["spark", "spark.pandas"])
def test_backtest_computes_features_from_complete_history(spark, history_payload, monkeypatch, backend):
    @feature(source="loans")
    def total(rows) -> int:
        return sum(row["amount"] for row in rows)

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    monkeypatch.setattr(Payload, "from_yaml", lambda path: history_payload)
    driver = spark.createDataFrame([
        ("U1", day(9)), ("U1", day(10)), ("U1", day(20)), ("U2", day(20)),
    ], "user_id string, as_of timestamp")

    result = Backtest("unused.yaml", backend=backend).compute(driver, ["total", "count"])

    assert {(row.user_id, row.as_of.day): (row.total, row["count"])
            for row in result.collect()} == {
        ("U1", 9): (100, 1), ("U1", 10): (140, 1), ("U1", 20): (375, 2), ("U2", 20): (0, 0),
    }


def test_shared_nested_entity_stays_isolated_per_parent_and_observation(spark, payload):
    agreement = PayloadSection(
        name="agreement", dwh_table="pit_agreements",
        fields={"agreement_id": Field("string", primary_key=True), "term": Field("int32"),
                "createdat": Field("timestamp", available_at=True)},
        relationship=Relationship("many-to-one", [JoinKey("agreement_id", "agreement_id")]),
    )
    payload.root.children["loans"].children["agreement"] = agreement
    spark.createDataFrame(
        [("A1", 12, day(10))], "agreement_id string, term int, createdat timestamp"
    ).createOrReplaceTempView("pit_agreements")
    cells = fetch_cells(spark, payload, [("U1", day(1)), ("U1", day(15))])
    assert cells["U1", day(1)]["loans"][0]["agreement"] is None
    assert [loan["agreement"]["term"] for loan in cells["U1", day(15)]["loans"]] == [12, 12]


@pytest.mark.usefixtures("isolated_feature_registry")
def test_retrieved_online_and_offline_payloads_compute_identical_features(spark, payload):
    @field(source="loans")
    def amount_usd(row) -> float:
        return row["amount"] / 7

    @feature(source="loans")
    def total(rows) -> float:
        return sum(row["amount_usd"] for row in rows)

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    historical = fetch_cells(spark, payload, [("U1", day(15))])["U1", day(15)]
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [tuple(loan.values()) for loan in historical["loans"]]
    with patch("mirus.serving.mysql.datetime") as clock:
        clock.now.return_value = day(15).replace(tzinfo=timezone.utc)
        online = OnlineFetcher(payload, connection).fetch({"user_id": "U1"})
    assert online == historical
    plan = compile_offline(["total", "count"])
    assert compute_features(online, ["total", "count"]) == compute_features(historical, catalog=plan.catalog)
    result = compute_features(historical, catalog=plan.catalog)
    assert result["total"] == pytest.approx(300 / 7)
    assert result["count"] == 2
