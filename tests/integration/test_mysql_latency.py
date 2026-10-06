"""Opt-in latency test for MySQLFetcher against a real MySQL server.

Run with MYSQL_LATENCY_TEST=1 and the MYSQL_* connection variables documented
in README.md. The measured interval covers the complete fetch() call.
"""

import math
import os
import time
from datetime import datetime

import pytest

from mirus.serving import MySQLConnection, OnlineFetcher
from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship

TABLE = "mirus_mysql_fetcher_latency"
pytestmark = pytest.mark.skipif(
    os.getenv("MYSQL_LATENCY_TEST") != "1",
    reason="set MYSQL_LATENCY_TEST=1 to run the MySQL latency test",
)


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _percentile(samples: list[float], percentile: float) -> float:
    """Return the nearest-rank percentile, suitable for latency SLO checks."""
    ordered = sorted(samples)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[rank - 1]


@pytest.fixture(scope="module")
def mysql_fetcher():
    database = os.getenv("MYSQL_DATABASE")
    if not database:
        pytest.fail("MYSQL_DATABASE is required when MYSQL_LATENCY_TEST=1")

    try:
        connection = MySQLConnection(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=database,
            charset="utf8mb4",
            autocommit=False,
        ).connect()
    except RuntimeError as error:
        pytest.skip(str(error))

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TEMPORARY TABLE `{TABLE}` (
                `entity_id` VARCHAR(64) NOT NULL,
                `feature_id` INT NOT NULL,
                `feature_value` DOUBLE NOT NULL,
                `created_at` DATETIME(6) NOT NULL,
                PRIMARY KEY (`entity_id`, `feature_id`)
            )
            """
        )
        cursor.executemany(
            f"""
            INSERT INTO `{TABLE}`
                (`entity_id`, `feature_id`, `feature_value`, `created_at`)
            VALUES (%s, %s, %s, %s)
            """,
            [
                ("entity-1", feature_id, feature_id / 10, datetime(2020, 1, 1))
                for feature_id in range(20)
            ],
        )
    connection.commit()

    child = PayloadSection(
        name="features",
        fields={
            "entity_id": Field("string", primary_key=True),
            "feature_id": Field("int32", primary_key=True),
            "feature_value": Field("float64"),
            "created_at": Field("timestamp", available_at=True),
        },
        db_table=TABLE,
        relationship=Relationship(
            "one-to-many", [JoinKey("entity_id", "entity_id")]
        ),
    )
    fetcher = OnlineFetcher(
        Payload(
            name="mysql_latency",
            version=1,
            root=PayloadSection(
                name="payload",
                source="request",
                fields={
                    "entity_id": Field("string", primary_key=True),
                    "as_of": Field("timestamp", observation_time=True),
                },
                children={"features": child},
            ),
        ).validate(),
        connection,
    )
    yield fetcher
    connection.close()


def test_fetch_latency_meets_online_serving_budget(mysql_fetcher):
    warmups = _positive_int("MYSQL_LATENCY_WARMUPS", 20)
    iterations = _positive_int("MYSQL_LATENCY_ITERATIONS", 200)
    p95_budget_ms = _positive_float("MYSQL_LATENCY_P95_MS", 20)
    p99_budget_ms = _positive_float("MYSQL_LATENCY_P99_MS", 35)
    request = {"entity_id": "entity-1"}

    for _ in range(warmups):
        result = mysql_fetcher.fetch(request)
    assert len(result["features"]) == 20

    samples_ms = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = mysql_fetcher.fetch(request)
        samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)

    assert len(result["features"]) == 20
    p50 = _percentile(samples_ms, 50)
    p95 = _percentile(samples_ms, 95)
    p99 = _percentile(samples_ms, 99)
    print(
        f"\nMySQLFetcher latency over {iterations} requests: "
        f"p50={p50:.2f}ms p95={p95:.2f}ms p99={p99:.2f}ms"
    )
    assert p95 <= p95_budget_ms, (
        f"p95 {p95:.2f}ms exceeds {p95_budget_ms:.2f}ms"
    )
    assert p99 <= p99_budget_ms, (
        f"p99 {p99:.2f}ms exceeds {p99_budget_ms:.2f}ms"
    )
