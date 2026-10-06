"""Feature decorators that record neutral declarations at module import time."""

from collections.abc import Sequence
from typing import Any, Callable, TypeVar

from . import registry
from .definitions import FeatureDeclaration, FieldDeclaration

Function = TypeVar("Function", bound=Callable[..., Any])
ParameterChoices = dict[str, Sequence[object]]


def _snapshot_parameters(
    parameters: ParameterChoices | None,
) -> tuple[tuple[str, object], ...]:
    """Freeze valid choice containers while preserving invalid input for compile."""
    snapshot = []
    for name, values in (parameters or {}).items():
        choices = (
            tuple(values)
            if isinstance(values, Sequence)
            and not isinstance(values, (str, bytes))
            else values
        )
        snapshot.append((name, choices))
    return tuple(snapshot)


def field(*, source: str) -> Callable[[Function], Function]:
    """Collect one row transformation without compiling it."""
    def decorate(function: Function) -> Function:
        declaration = FieldDeclaration(
            source=source,
            name=function.__name__,
            function=function,
        )
        registry.get_default_registry().register_field(declaration)
        return function
    return decorate


def feature(
    *,
    source: str,
    feature_name: str | None = None,
    parameters: ParameterChoices | None = None,
) -> Callable[[Function], Function]:
    """Declare a single feature, optionally expanded by parameter combinations."""
    def decorate(function: Function) -> Function:
        declaration = FeatureDeclaration(
            source=source,
            feature_name=feature_name,
            function=function,
            parameters=_snapshot_parameters(parameters),
        )
        registry.get_default_registry().register_feature(declaration)
        return function
    return decorate
