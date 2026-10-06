"""The online facade delegates independently of the offline protocol."""

from unittest.mock import patch

import pytest

from mirus.serving import OnlineFetcher


def test_online_fetcher_delegates_request_unchanged():
    payload, connection, request, output = object(), object(), {"user_id": "42"}, {"loans": []}
    with patch("mirus.serving.mysql.MySQLFetcher") as backend:
        backend.return_value.fetch.return_value = output
        fetcher = OnlineFetcher(payload, connection)
        assert fetcher.fetch(request) is output
        backend.assert_called_once_with(payload, connection)
        backend.return_value.fetch.assert_called_once_with(request)


def test_unknown_online_backend_is_explicit():
    with pytest.raises(NotImplementedError, match="available backends: mysql"):
        OnlineFetcher(None, None, backend="unknown")
