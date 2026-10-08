"""Pipeline selection and preparation without Spark or a warehouse."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mirus.backtest import Backtest
from mirus.features.decorators import feature
from mirus.payload import Field, Payload, PayloadSection


@pytest.fixture
def payload(monkeypatch):
    agreement = PayloadSection("agreement", {"term": Field("int32")})
    loans = PayloadSection("loans", {"amount": Field("int32")}, children={"agreement": agreement})
    definition = Payload("demo", 1, PayloadSection("payload", {"id": Field("string")},
                         children={"loans": loans, "devices": PayloadSection("devices", {})}))
    loader = Mock(return_value=definition)
    monkeypatch.setattr(Payload, "from_yaml", loader)
    return definition, loader


@pytest.fixture
def engine(monkeypatch):
    engine_class = Mock()
    monkeypatch.setitem(sys.modules, "mirus.backtest.spark.compute",
                        SimpleNamespace(SparkBacktest=engine_class))
    return engine_class


@pytest.mark.parametrize("backend, method", [("spark", "arrow"), ("spark.pandas", "pandas")])
def test_backend_selects_execution_method(payload, engine, backend, method):
    Backtest("payload.yaml", backend=backend)
    engine.assert_called_once_with(method=method)
    payload[1].assert_called_once_with("payload.yaml")


def test_default_backend_uses_arrow(payload, engine):
    Backtest("payload.yaml")
    engine.assert_called_once_with(method="arrow")


@pytest.mark.usefixtures("isolated_feature_registry")
@pytest.mark.parametrize("names, sources", [(["count"], ["loans"]), (None, ["loans", "devices"]), ([], [])])
def test_compute_prepares_selection_and_prunes_only_sources(payload, engine, names, sources):
    @feature(source="loans")
    def count(rows) -> int:
        raise AssertionError("Feature executed during preparation")

    @feature(source="devices")
    def device_count(rows) -> int:
        raise AssertionError("Feature executed during preparation")

    definition, _ = payload
    driver = Mock()
    result = Backtest("payload.yaml").compute(driver, names)
    given_driver, selected, plan = engine.return_value.compute.call_args.args

    assert given_driver is driver
    assert list(selected.root.children) == sources
    assert plan.feature_names == tuple(["count", "device_count"] if names is None else names)
    assert plan.required_sources == tuple(sources)
    if "loans" in sources:
        assert selected.root.children["loans"] is definition.root.children["loans"]
    assert list(definition.root.children) == ["loans", "devices"]
    assert result is engine.return_value.compute.return_value
    engine.return_value.compute.assert_called_once()
    driver.collect.assert_not_called()
    driver.count.assert_not_called()
    driver.sparkSession.conf.set.assert_not_called()


@pytest.mark.usefixtures("isolated_feature_registry")
def test_reusing_pipeline_loads_yaml_once_and_prepares_each_selection(payload, engine):
    @feature(source="loans")
    def count(rows) -> int:
        return len(rows)

    backtest = Backtest("payload.yaml")
    driver = Mock()
    backtest.compute(driver, ["count"])
    backtest.compute(driver, [])

    @feature(source="devices")
    def device_count(rows) -> int:
        return len(rows)

    backtest.compute(driver)
    payload[1].assert_called_once_with("payload.yaml")
    calls = engine.return_value.compute.call_args_list
    assert [call.args[2].feature_names for call in calls] == [
        ("count",), (), ("count", "device_count"),
    ]
    assert [list(call.args[1].root.children) for call in calls] == [
        ["loans"], [], ["loans", "devices"],
    ]


def test_unknown_backend_fails_before_reading_yaml_or_importing_spark(monkeypatch):
    loader = Mock()
    monkeypatch.setattr(Payload, "from_yaml", loader)
    monkeypatch.setitem(sys.modules, "mirus.backtest.spark.compute", None)
    with pytest.raises(NotImplementedError, match="available backends: spark, spark.pandas"):
        Backtest("payload.yaml", backend="unknown")
    loader.assert_not_called()
