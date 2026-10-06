from typing import Generic, Protocol, TypeVar

from mirus.payload import Payload

DriverT = TypeVar("DriverT")
FrameT = TypeVar("FrameT")
DriverIn = TypeVar("DriverIn", contravariant=True)
FrameOut = TypeVar("FrameOut", covariant=True)


class OfflineBackend(Protocol[DriverIn, FrameOut]):
    """Offline backends turn driver observations into payload-bearing frames."""

    def fetch(self, driver: DriverIn) -> FrameOut: ...


class OfflineFetcher(Generic[DriverT, FrameT]):
    """Stable offline facade that hides the active processing implementation."""

    available_backends = ("spark",)

    def __init__(self, payload: Payload, session, *, backend: str = "spark"):
        self._implementation: OfflineBackend[DriverT, FrameT] = self._create_implementation(
            backend, payload, session
        )

    @staticmethod
    def _create_implementation(backend: str, payload: Payload, session):
        """Create the selected backend without exposing its class to callers."""
        if backend == "spark":
            # Import lazily so importing the public interface does not load Spark.
            from .spark.fetcher import SparkFetcher

            return SparkFetcher(payload, session)

        # Future offline backends (for example Flink or a SQL warehouse) are
        # added here without changing the public OfflineFetcher API.
        available = ", ".join(OfflineFetcher.available_backends)
        raise NotImplementedError(
            f"offline backend {backend!r} is not implemented; "
            f"available backends: {available}"
        )

    def fetch(self, driver: DriverT) -> FrameT:
        """Build payloads using the configured offline implementation."""
        return self._implementation.fetch(driver)
