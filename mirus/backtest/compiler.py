"""Compile raw declarations into a Spark-free offline feature plan."""

from collections.abc import Sequence
from functools import partial
from typing import get_type_hints

from mirus.features.compiler import compile_selection
from mirus.features.definitions import CompiledFeature, DeclarationSnapshot
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
    snapshot: DeclarationSnapshot,
    feature_names: Sequence[str] | None = None,
) -> OfflinePlan:
    """Compile one fresh offline artifact without importing Spark."""
    selection = compile_selection(snapshot, feature_names)
    return OfflinePlan(
        sources=selection.sources,
        feature_names=selection.feature_names,
        dependencies=selection.dependencies,
        output_types=tuple((feature.name, _scalar_output_type(feature)) for feature in selection.features),
    )
