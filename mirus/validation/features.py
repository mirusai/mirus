"""Validate registered feature definitions without executing user callables."""

from collections.abc import Sequence
from inspect import signature
from typing import get_type_hints

from mirus.features import registry
from mirus.features.compiler import _feature_arguments
from mirus.payload import Payload


def _validate_call(function, arguments):
    try:
        signature(function).bind(None, **arguments)
    except TypeError as error:
        raise TypeError(f"{function.__name__} cannot bind declared parameters: {error}") from error


def _validate_source(source: str, payload: Payload):
    # Nested data stays inside each source record; dotted paths are not source selectors.
    if source not in payload.root.children:
        raise ValueError(f"Unknown feature source: {source!r}; expected a top-level payload section")


def validate_features(payload: Payload) -> None:
    """Check every imported declaration, including currently unselected features.

    Features require resolvable return annotations for shared offline metadata.
    Field annotations are optional; neither fields nor features are executed.
    """
    field_declarations, feature_declarations = registry.default_registry.snapshot()
    fields, names = set(), set()
    for declaration in field_declarations:
        _validate_source(declaration.source, payload)
        key = (declaration.source, declaration.name)
        if key in fields:
            raise ValueError(f"Duplicate field: {key}")
        fields.add(key)
        _validate_call(declaration.function, {})
    for declaration in feature_declarations:
        _validate_source(declaration.source, payload)
        for name, values in declaration.parameters:
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
                raise ValueError(f"parameter {name!r} must contain a non-empty sequence of choices")
        if declaration.parameters and declaration.feature_name is None:
            raise ValueError("parameters require a feature_name template")
        _validate_call(declaration.function, {name: values[0] for name, values in declaration.parameters})
        for name, _ in _feature_arguments(declaration):
            if name in names:
                raise ValueError(f"Duplicate feature: {name}")
            names.add(name)
        if "return" not in get_type_hints(declaration.function):
            raise TypeError(f"{declaration.function.__name__} needs a return annotation")
