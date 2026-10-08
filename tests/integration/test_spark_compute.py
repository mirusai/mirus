"""Actual worker execution: Arrow and pandas must match online computation."""

import os
from datetime import datetime
from decimal import Decimal

import pytest

from mirus.backtest import Backtest, compile_offline
from mirus.features.compute import compute_features
from mirus.features.decorators import feature, field
from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship

pytestmark = [pytest.mark.usefixtures("isolated_feature_registry"), pytest.mark.skipif(
    os.getenv("SPARK_INTEGRATION_TEST") != "1", reason="set SPARK_INTEGRATION_TEST=1")]


@pytest.fixture(scope="module")
def spark():
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from pyspark.sql import SparkSession
    from mirus.backtest.spark.config import RECOMMENDED_SPARK_CONFIG

    builder = SparkSession.builder.master("local[2]").appName("mirus-udf-parity")
    for key, value in RECOMMENDED_SPARK_CONFIG.items():
        builder = builder.config(key, value)
    session = (builder
               .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2")
               .config("spark.sql.execution.arrow.maxRecordsPerBatch", "2")
               .getOrCreate())
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def feature_names():
    @field(source="loans")
    def adjusted_amount(row):
        assert isinstance(row["payments"], list)
        assert row["agreement"] is None or isinstance(row["agreement"], dict)
        assert isinstance(row["as_of"], datetime)
        assert isinstance(row["createdat"], datetime)
        return float(row["amount"]) + sum(float(item["amount"]) for item in row["payments"])

    @feature(source="loans", feature_name="amount_{days}d", parameters={"days": [7, 30]})
    def amount(rows, *, days) -> float:
        return sum((row["adjusted_amount"] for row in rows
                    if (row["as_of"] - row["createdat"]).days < days), 0.0)

    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    @feature(source="loans")
    def exact_id(rows) -> str | None:
        return rows[0]["external_id"] if rows else None

    @feature(source="loans")
    def latest(rows) -> datetime | None:
        return max((row["createdat"] for row in rows), default=None)

    @feature(source="loans")
    def version(rows) -> int | None:
        return next((row["agreement"]["version"] for row in rows if row["agreement"]), None)

    @feature(source="devices")
    def unused(rows):
        raise AssertionError("Unselected feature executed")

    return ["amount_30d", "count", "exact_id", "amount_7d", "latest", "version"]


@pytest.mark.parametrize("method", ["arrow", "pandas"])
def test_nested_batches_match_online_with_empty_and_null_sources(spark, method):
    from mirus.backtest.spark.udf import score_payloads

    names = feature_names()
    as_of = datetime(2026, 1, 15)
    created = datetime(2026, 1, 10)
    loan = {"amount": Decimal("7.00"), "createdat": created,
            "agreement": {"version": 12},
            "payments": [{"amount": Decimal("1.00")}, {"amount": Decimal("2.00")}]}
    payloads = [
        {"as_of": as_of, "external_id": "9007199254740995", "loans": [loan]},
        {"as_of": as_of, "external_id": None, "loans": [loan]},
        {"as_of": as_of, "external_id": "7", "loans": []},
        {"as_of": as_of, "external_id": None, "loans": None},
        {"as_of": as_of, "external_id": "9", "loans": [{**loan, "agreement": None, "payments": []}]},
    ]
    frame = spark.createDataFrame(list(enumerate(payloads)),
        "id long, payload struct<as_of:timestamp,external_id:string,loans:array<struct<"
        "amount:decimal(18,2),createdat:timestamp,agreement:struct<version:long>,"
        "payments:array<struct<amount:decimal(18,2)>>>>> ").repartition(2)
    plan = compile_offline(names)
    result = score_payloads(frame, plan, method=method)
    assert result.columns == ["id", *names]
    for row in result.collect():
        values = row.asDict()
        identifier = values.pop("id")
        assert values == compute_features(payloads[identifier], catalog=plan.catalog)
    assert ("ArrowEvalPython" in result._jdf.queryExecution().executedPlan().toString())


@pytest.mark.parametrize("backend", ["spark", "spark.pandas"])
def test_backtest_pit_prunes_unused_table_and_preserves_empty_observations(spark, monkeypatch, backend):
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    @feature(source="unused")
    def unused(rows) -> int:
        raise AssertionError("Unused feature executed")

    loans = PayloadSection("loans", {
        "loan_id": Field("string", primary_key=True), "user_id": Field("string"),
        "createdat": Field("timestamp", available_at=True)},
        dwh_table="compute_loans", relationship=Relationship("one-to-many", [JoinKey("user_id", "user_id")]))
    payload = Payload("test", 1, PayloadSection("payload", {
        "user_id": Field("string", primary_key=True), "as_of": Field("timestamp", observation_time=True)},
        children={"loans": loans, "unused": PayloadSection("unused", {}, dwh_table="missing_table")}))
    monkeypatch.setattr(Payload, "from_yaml", lambda path: payload)
    spark.createDataFrame([("L1", "U1", datetime(2026, 1, 10))],
                          "loan_id string, user_id string, createdat timestamp").createOrReplaceTempView("compute_loans")
    driver = spark.createDataFrame([
        ("U1", datetime(2026, 1, 9)), ("U1", datetime(2026, 1, 10)), ("U2", datetime(2026, 1, 15))],
        "user_id string, as_of timestamp")
    result = Backtest("unused.yaml", backend=backend).compute(driver, feature_names=["count"])
    assert {(row.user_id, row.as_of.day): row["count"] for row in result.collect()} == {
        ("U1", 9): 0, ("U1", 10): 1, ("U2", 15): 0}
    assert list(payload.root.children) == ["loans", "unused"]


@pytest.mark.parametrize("backend", ["spark", "spark.pandas"])
def test_none_selection_computes_every_registered_feature(spark, monkeypatch, backend):
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    @feature(source="loans")
    def total(rows) -> float:
        return sum((float(row["amount"]) for row in rows), 0.0)

    loans = PayloadSection("loans", {
        "id": Field("string", primary_key=True), "user_id": Field("string"),
        "amount": Field("int32"), "createdat": Field("timestamp", available_at=True)},
        dwh_table="all_features_loans", relationship=Relationship("one-to-many", [JoinKey("user_id", "user_id")]))
    payload = Payload("test", 1, PayloadSection("payload", {
        "user_id": Field("string", primary_key=True), "as_of": Field("timestamp", observation_time=True)},
        children={"loans": loans}))
    monkeypatch.setattr(Payload, "from_yaml", lambda path: payload)
    spark.createDataFrame([("L1", "U1", 100, datetime(2026, 1, 10))],
                          "id string, user_id string, amount int, createdat timestamp").createOrReplaceTempView("all_features_loans")
    driver = spark.createDataFrame([("U1", datetime(2026, 1, 15)), ("U2", datetime(2026, 1, 15))],
                                   "user_id string, as_of timestamp")
    result = Backtest("unused.yaml", backend=backend).compute(driver)
    assert driver.sparkSession.conf.get("spark.sql.execution.arrow.maxRecordsPerBatch") == "2"
    assert driver.sparkSession.conf.get("spark.sql.shuffle.partitions") == "2"
    assert result.columns == ["user_id", "as_of", "count", "total"]
    assert {row.user_id: (row["count"], row.total) for row in result.collect()} == {
        "U1": (1, 100.0), "U2": (0, 0.0)}


@pytest.mark.parametrize("backend", ["spark", "spark.pandas"])
def test_backtest_empty_selection_skips_missing_tables_and_udf(spark, monkeypatch, backend):
    payload = Payload("test", 1, PayloadSection("payload", {
        "user_id": Field("string", primary_key=True), "as_of": Field("timestamp", observation_time=True)},
        children={"unused": PayloadSection("unused", {}, dwh_table="missing_table")}))
    monkeypatch.setattr(Payload, "from_yaml", lambda path: payload)
    driver = spark.createDataFrame([("U1", datetime(2026, 1, 15))], "user_id string, as_of timestamp")

    result = Backtest("unused.yaml", backend=backend).compute(driver, [])

    assert result.columns == ["user_id", "as_of"]
    assert [row.user_id for row in result.collect()] == ["U1"]
    assert "EvalPython" not in result._jdf.queryExecution().executedPlan().toString()
