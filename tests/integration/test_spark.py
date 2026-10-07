"""Integration test for the nested payload produced by SparkFetcher."""

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mirus.payload import Payload
from mirus.validation import validate_payload

try:
    from pyspark.sql import SparkSession
except ImportError:
    SparkSession = None


PAYLOAD_YAML = Path(__file__).resolve().parents[1] / "fixtures/payload.yaml"
AS_OF = datetime(2026, 10, 5, 12, 0)
CREATED = datetime(2026, 10, 1, 9, 0)
pytestmark = pytest.mark.skipif(
    os.getenv("SPARK_INTEGRATION_TEST") != "1",
    reason="set SPARK_INTEGRATION_TEST=1 to run the Spark integration test",
)


@pytest.fixture(scope="class")
def spark(request):
    if SparkSession is None:
        pytest.skip("install the spark extra")
    session = (
        SparkSession.builder.master("local[1]")
        .appName("mirus-backtest-fetcher-test")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    request.cls.spark = session
    yield session
    session.stop()


@pytest.mark.usefixtures("spark")
class TestOfflineSparkIntegration:
    def _view(self, name, rows, columns):
        self.spark.createDataFrame(rows, columns).createOrReplaceTempView(name)

    def test_constructs_complete_nested_payload_cell(self):
        from mirus.backtest.spark.fetcher import SparkFetcher

        payload = validate_payload(Payload.from_yaml(PAYLOAD_YAML))
        sections = payload.root.children

        # Temporary views keep the integration test isolated from a warehouse.
        sections["loans"].dwh_table = "loan_record_history"
        sections["loans"].children["agreement"].dwh_table = "loan_agreement_history"
        sections["devices"].dwh_table = "user_device_history"
        sections["login_behavior"].dwh_table = "login_event_history"
        sections["third_party_data"].dwh_table = "third_party_report_history"

        self._view(
            "loan_record_history",
            [
                (
                    "tenant-1",
                    "loan-1",
                    "user-42",
                    "agreement-1",
                    Decimal("10000.00"),
                    CREATED,
                ),
                (
                    "tenant-1",
                    "loan-2",
                    "user-42",
                    "agreement-2",
                    Decimal("2500.00"),
                    CREATED,
                ),
            ],
            [
                "tenant_id",
                "loan_id",
                "customer_id",
                "agreement_id",
                "loan_amount",
                "created_at",
            ],
        )
        self._view(
            "loan_agreement_history",
            [
                ("tenant-1", "agreement-1", 12, Decimal("0.085000"), CREATED),
                ("tenant-1", "agreement-2", 6, Decimal("0.070000"), CREATED),
            ],
            [
                "tenant_id",
                "agreement_id",
                "term_months",
                "interest_rate",
                "createdat",
            ],
        )
        self._view(
            "user_device_history",
            [
                ("tenant-1", "user-42", "device-1", "mobile", CREATED),
                ("tenant-1", "user-42", "device-2", "desktop", CREATED),
            ],
            [
                "tenant_id",
                "user_id",
                "device_id",
                "device_type",
                "createdat",
            ],
        )
        self._view(
            "login_event_history",
            [
                ("tenant-1", "login-1", "user-42", "192.0.2.10", CREATED),
                ("tenant-1", "login-2", "user-42", "192.0.2.20", CREATED),
            ],
            [
                "tenant_id",
                "login_id",
                "user_id",
                "ip_address",
                "createdat",
            ],
        )
        self._view(
            "third_party_report_history",
            [("tenant-1", "report-1", "user-42", "demo_bureau", 720.0, CREATED)],
            [
                "tenant_id",
                "report_id",
                "user_id",
                "provider",
                "score",
                "createdat",
            ],
        )
        driver = self.spark.createDataFrame(
            [("application-123", "tenant-1", "user-42", AS_OF)],
            ["observation_id", "tenant_id", "user_id", "as_of"],
        )

        frame = SparkFetcher(payload, self.spark).fetch(driver)
        result = frame.collect()

        assert len(result) == 1
        cell = result[0]["payload"].asDict(recursive=True)
        assert cell["observation_id"] == "application-123"
        assert cell["as_of"] == AS_OF
        assert [loan["loan_id"] for loan in cell["loans"]] == [
            "loan-1", "loan-2"
        ]
        assert cell["loans"][0]["agreement"]["agreement_id"] == "agreement-1"
        assert [device["device_id"] for device in cell["devices"]] == [
            "device-1", "device-2"
        ]
        assert [event["login_id"] for event in cell["login_behavior"]] == [
            "login-1", "login-2"
        ]
        assert cell["third_party_data"][0]["score"] == 720.0
