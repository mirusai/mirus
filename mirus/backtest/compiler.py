"""Compile raw declarations into a Spark-free offline feature plan."""

from collections.abc import Sequence
from functools import partial
from typing import get_type_hints

from mirus.features.compiler import prepare_features
from mirus.features.definitions import CompiledFeature
from .plan import OfflinePlan


def _scalar_output_type(feature: CompiledFeature) -> object:
    function = feature.function
    while isinstance(function, partial):
        function = function.func
    hints = get_type_hints(function)
    if "return" not in hints:
        raise TypeError(f"{function.__name__} needs a return annotation")
    return hints["return"]


def compile_offline(
    feature_names: Sequence[str] | None = None,
) -> OfflinePlan:
    """Compile a fresh Spark-free artifact from current declarations."""
    catalog = prepare_features(feature_names)
    return OfflinePlan(
        catalog=catalog,
        output_types=tuple(
            (name, _scalar_output_type(feature))
            for name, feature in catalog.features_by_name.items()
        ),
    )
