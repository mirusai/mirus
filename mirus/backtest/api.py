"""Public offline feature compilation API."""

from collections.abc import Sequence

from mirus.features import registry
from .compiler import compile_offline as _compile_offline
from .plan import OfflinePlan


def compile_offline(
    feature_names: Sequence[str] | None = None,
) -> OfflinePlan:
    """Compile a fresh Spark-free artifact from current declarations."""
    snapshot = registry.get_default_registry().snapshot()
    return _compile_offline(snapshot, feature_names)
