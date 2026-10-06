"""Engine-neutral output type inspection; Spark conversion is not implemented yet."""

from collections.abc import Sequence

from .api import compile_offline


def feature_schema(
    feature_names: Sequence[str] | None = None,
) -> dict[str, object]:
    """Return declared output names and types without running user functions."""
    return compile_offline(feature_names).output_schema
