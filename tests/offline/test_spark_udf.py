"""Pure Spark adapter checks: types, normalization and engine delegation."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
T = pytest.importorskip("pyspark.sql.types")

from mirus.backtest.spark.udf import _normalize, _spark_type, score_payloads


@pytest.mark.parametrize("annotation, expected", [
    (int, T.LongType()), (float | None, T.DoubleType()), (bool, T.BooleanType()),
    (str, T.StringType()), (bytes, T.BinaryType()),
    (date, T.DateType()), (datetime | None, T.TimestampType()),
])
def test_scalar_output_schema(annotation, expected):
    assert _spark_type(annotation) == expected


@pytest.mark.parametrize("annotation", [dict, list[int], int | str, type(None)])
def test_unsupported_output_type_is_explicit(annotation):
    with pytest.raises(TypeError, match="Unsupported feature output"):
        _spark_type(annotation)


def test_normalization_preserves_nested_shape_types_and_input():
    timestamp = pd.Timestamp("2026-01-01")
    loans = np.array([{"amount": Decimal("7.00"), "agreement": {"term": np.int64(12)},
                       "payments": np.array([{"at": timestamp}, {"at": pd.NaT}])}], dtype=object)
    raw = {"as_of": timestamp, "loans": loans, "empty": np.array([], dtype=object),
           "missing": None, "id": np.int64(2**53 + 1)}
    normalized = _normalize(raw)
    assert normalized == {"as_of": datetime(2026, 1, 1), "loans": [
        {"amount": Decimal("7.00"), "agreement": {"term": 12},
         "payments": [{"at": datetime(2026, 1, 1)}, {"at": None}]}],
        "empty": [], "missing": None, "id": 2**53 + 1}
    assert raw["loans"] is loans
    assert isinstance(raw["loans"][0]["payments"], np.ndarray)
    assert type(normalized["id"]) is int


def test_invalid_method_and_empty_selection_do_not_build_udfs():
    frame, plan = Mock(), Mock(feature_names=())
    with pytest.raises(ValueError, match="method"):
        score_payloads(frame, plan, method="unknown")
    assert score_payloads(frame, plan) is frame.drop.return_value
    frame.drop.assert_called_once_with("payload")


@pytest.mark.parametrize("method", ["arrow", "pandas"])
def test_spark_engine_retrieves_payloads_then_scores_without_actions(monkeypatch, method):
    from mirus.backtest.spark import compute

    fetcher = Mock()
    scorer = Mock()
    monkeypatch.setattr(compute, "SparkFetcher", fetcher)
    monkeypatch.setattr(compute, "score_payloads", scorer)
    driver = Mock()
    payload, plan = Mock(), Mock()
    result = compute.SparkBacktest(method=method).compute(driver, payload, plan)

    fetcher.assert_called_once_with(payload, driver.sparkSession)
    fetcher.return_value.fetch.assert_called_once_with(driver)
    scorer.assert_called_once_with(fetcher.return_value.fetch.return_value, plan, method=method)
    assert result is scorer.return_value
    driver.collect.assert_not_called()
    driver.count.assert_not_called()
