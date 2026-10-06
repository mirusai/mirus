from typing import Any, Protocol

from mirus.payload import Payload


class OnlineBackend(Protocol):
    """Online backends return one nested Python payload per request."""

    def fetch(self, request: dict[str, Any]) -> dict[str, Any]: ...


class OnlineFetcher:
    """Stable online facade that hides the active serving implementation."""

    available_backends = ("mysql",)

    def __init__(self, payload: Payload, connection, *, backend: str = "mysql"):
        self._implementation: OnlineBackend = self._create_implementation(
            backend, payload, connection
        )

    @staticmethod
    def _create_implementation(backend: str, payload: Payload, connection):
        """Create the selected backend without exposing its class to callers."""
        if backend == "mysql":
            # Import lazily so the public interface has no PyMySQL dependency.
            from .mysql import MySQLFetcher

            return MySQLFetcher(payload, connection)

        # Future online backends (for example Redis or DynamoDB) are added here
        # without changing the public OnlineFetcher API.
        available = ", ".join(OnlineFetcher.available_backends)
        raise NotImplementedError(
            f"online backend {backend!r} is not implemented; "
            f"available backends: {available}"
        )

    def fetch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Fetch a Python payload using the configured online implementation."""
        return self._implementation.fetch(request)
