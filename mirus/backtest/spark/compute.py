"""Spark implementation of historical retrieval and feature execution."""

from pyspark.sql import DataFrame

from mirus.backtest.plan import OfflinePlan
from mirus.payload import Payload

from .fetcher import SparkFetcher
from .udf import score_payloads


class SparkBacktest:
    """Execute a prepared selection using native PIT joins and one Python UDF."""

    def __init__(self, *, method: str = "arrow"):
        self.method = method

    def compute(self, driver: DataFrame, payload: Payload, plan: OfflinePlan) -> DataFrame:
        # Retrieval uses native Spark operations; Python runs only inside the UDF.
        frame = SparkFetcher(payload, driver.sparkSession).fetch(driver)
        return score_payloads(frame, plan, method=self.method)
