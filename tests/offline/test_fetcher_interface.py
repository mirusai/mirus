"""Offline facade tests need neither a Spark session nor a warehouse."""

import sys
from types import SimpleNamespace

import pytest

from mirus.backtest import OfflineFetcher


def test_offline_fetcher_delegates_to_selected_backend(monkeypatch):
    payload, session, driver, output = object(), object(), object(), object()

    class Backend:
        def __init__(self, given_payload, given_session):
            assert given_payload is payload
            assert given_session is session

        def fetch(self, given_driver):
            assert given_driver is driver
            return output

    monkeypatch.setitem(sys.modules, "mirus.backtest.spark.fetcher", SimpleNamespace(SparkFetcher=Backend))
    assert OfflineFetcher(payload, session).fetch(driver) is output


def test_unknown_offline_backend_fails_before_importing_spark():
    with pytest.raises(NotImplementedError, match="available backends: spark"):
        OfflineFetcher(None, None, backend="unknown")
