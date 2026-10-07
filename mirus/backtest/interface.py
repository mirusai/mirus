"""Public backtest pipeline; computation backends are selected internally."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from mirus.payload import Payload

from .compiler import compile_offline
from .plan import OfflinePlan

DriverT = TypeVar("DriverT")
FrameT = TypeVar("FrameT")
DriverIn = TypeVar("DriverIn", contravariant=True)
FrameOut = TypeVar("FrameOut", covariant=True)


class BacktestBackend(Protocol[DriverIn, FrameOut]):
    """Backends retrieve historical payloads and execute prepared features."""

    def compute(self, driver: DriverIn, payload: Payload, plan: OfflinePlan) -> FrameOut: ...


class Backtest(Generic[DriverT, FrameT]):
    """Prepare feature selections and execute them through a chosen backend.

    Import user feature modules before computing. Each call returns a lazy
    backend result; it does not execute or persist the job.
    """

    available_backends = ("spark", "spark.pandas")

    def __init__(self, payload_yaml: str | Path, *, backend: str = "spark"):
        if backend not in self.available_backends:
            raise NotImplementedError(
                f"backtest backend {backend!r} is not implemented; "
                f"available backends: {', '.join(self.available_backends)}"
            )

        self.payload = Payload.from_yaml(payload_yaml)

        # Import Spark only when constructing a Spark-backed pipeline.
        from .spark.compute import SparkBacktest

        method = "pandas" if backend == "spark.pandas" else "arrow"
        self._implementation: BacktestBackend[DriverT, FrameT] = SparkBacktest(method=method)

    def compute(
        self, driver: DriverT, feature_names: Sequence[str] | None = None,
    ) -> FrameT:
        """Compute selected features; None selects all, and [] selects none."""
        plan = compile_offline(feature_names)

        # Retain complete nested sources, but skip all unused source joins.
        children = {name: self.payload.root.children[name] for name in plan.required_sources}
        selected = replace(self.payload, root=replace(self.payload.root, children=children))

        return self._implementation.compute(driver, selected, plan)
