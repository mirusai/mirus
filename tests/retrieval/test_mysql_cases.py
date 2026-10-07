"""Focused online retrieval tests for empty data, cursors and connection cleanup."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mirus.payload import Payload
from mirus.serving import MySQLConnection, OnlineFetcher
from mirus.validation import validate_payload


@pytest.fixture
def retrieval():
    payload = validate_payload(Payload.from_yaml(
        Path(__file__).resolve().parents[1] / "fixtures/payload.yaml"
    ))
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    return payload, connection, cursor


def request():
    return {"observation_id": "o1", "tenant_id": "t1", "user_id": "u1"}


def test_empty_lists_and_missing_object_skip_descendant_queries(retrieval):
    payload, connection, cursor = retrieval
    payload.root.children["third_party_data"].relationship.cardinality = "one-to-one"
    cursor.fetchall.return_value = []
    result = OnlineFetcher(payload, connection).fetch(request())
    assert result["loans"] == result["devices"] == result["login_behavior"] == []
    assert result["third_party_data"] is None
    assert cursor.fetchall.call_count == 4  # No agreement read when there are no loans.
    connection.rollback.assert_called_once()


def test_null_join_key_skips_all_reads(retrieval):
    payload, connection, cursor = retrieval
    result = OnlineFetcher(payload, connection).fetch({**request(), "tenant_id": None})
    assert all(result[name] == [] for name in payload.root.children)
    cursor.fetchall.assert_not_called()
    connection.rollback.assert_called_once()


def test_dictionary_cursor_preserves_payload_columns(retrieval):
    payload, connection, cursor = retrieval
    devices = payload.root.children["devices"]
    payload.root.children = {"devices": devices}
    row = dict(zip(devices.fields, ("t1", "u1", "d1", "mobile", datetime(2026, 1, 1))))
    cursor.fetchall.return_value = [row]
    assert OnlineFetcher(payload, connection).fetch(request())["devices"] == [row]


def test_query_failure_rolls_back_before_connection_reuse(retrieval):
    payload, connection, cursor = retrieval
    cursor.fetchall.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError, match="query failed"):
        OnlineFetcher(payload, connection).fetch(request())
    connection.rollback.assert_called_once()


def test_failed_warm_up_closes_new_connection():
    connection = MagicMock()
    connection.cursor.side_effect = RuntimeError("warm-up failed")
    with patch("mirus.serving.mysql.import_module") as load:
        load.return_value.connect.return_value = connection
        with pytest.raises(RuntimeError, match="warm-up failed"):
            MySQLConnection().connect()
    connection.close.assert_called_once()
