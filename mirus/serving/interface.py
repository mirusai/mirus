from typing import Any, Protocol

from mirus.payload import Payload


class OnlineBackend(Protocol):
    """Online backends return one nested Python payload per request."""

    def fetch(self, request: dict[str, Any]) -> dict[str, Any]: ...


class OnlineFetcher:
    """Stable online facade that hides the active serving implementation."""

    available_backends = ("mysql",)

    def __init__(self, payload: Payload, connection, *, backend: str = "mysql"):
        if backend != "mysql":
            raise NotImplementedError(
                f"online backend {backend!r} is not implemented; "
                f"available backends: {', '.join(self.available_backends)}"
            )
        from .mysql import MySQLFetcher

        self._implementation: OnlineBackend = MySQLFetcher(payload, connection)

    def fetch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Fetch a Python payload using the configured online implementation."""
        return self._implementation.fetch(request)
