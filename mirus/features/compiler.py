"""Prepare reusable feature definitions without executing user functions."""

from collections.abc import Sequence
from functools import partial
from inspect import signature
from itertools import product

from . import registry
from .definitions import (
    CompiledFeature, CompiledField, DeclarationSnapshot,
    FeatureCatalog, FeatureDeclaration,
)


def _compile_fields(snapshot: DeclarationSnapshot) -> dict[str, tuple[CompiledField, ...]]:
    """Group row transforms by source and reject a repeated source/name pair."""
    grouped = {}
    seen = set()

    for declaration in snapshot.fields:
        # The same field name on one source is a conflict, even when unselected.
        key = (declaration.source, declaration.name)
        if key in seen:
            raise ValueError(f"Duplicate field: {key}")
        seen.add(key)

        grouped.setdefault(declaration.source, []).append(
            CompiledField(declaration.source, declaration.name, declaration.function)
        )

    # Freeze each source so a prepared catalog cannot be appended to later.
    return {source: tuple(fields) for source, fields in grouped.items()}


def _feature_arguments(declaration: FeatureDeclaration):
    """Expand one declaration into output names and bound parameter values.

    A declaration without parameters yields its function name once. A
    parameterized declaration yields one name per combination of choices.
    """
    choices = declaration.parameters

    # Each parameter must offer at least one choice. A bare string is not a list of choices.
    for name, values in choices:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
            raise ValueError(f"parameter {name!r} must contain a non-empty sequence of choices")

    # Parameterized features need a template so each combination has its own name.
    if choices and declaration.feature_name is None:
        raise ValueError("parameters require a feature_name template")

    # Check the first combination now so a bad parameter name fails before expansion.
    if choices:
        try:
            signature(declaration.function).bind_partial(**{name: values[0] for name, values in choices})
        except TypeError as error:
            raise TypeError(
                f"{declaration.function.__name__} cannot bind declared parameters: {error}"
            ) from error

    template = declaration.feature_name or declaration.function.__name__
    names = tuple(name for name, _ in choices)

    # No choices still yield one empty combination, which keeps the single output name.
    for combination in product(*(values for _, values in choices)):
        arguments = dict(zip(names, combination))
        yield template.format(**arguments) if arguments else template, arguments


def prepare_features(feature_names: Sequence[str] | None = None) -> FeatureCatalog:
    """Snapshot the process registry and prepare that copy.

    Pass feature names to prepare only those outputs. Omit them to prepare
    every registered feature. User functions are not called.
    """
    # A bare string would be read one character at a time.
    if isinstance(feature_names, str):
        raise TypeError("feature_names must be a sequence of names, not a string")

    # Copy the registry before preparation, so a later registration cannot change this catalog.
    snapshot = registry.default_registry.snapshot()

    # None means every registered feature. Otherwise keep the requested order once.
    names = None if feature_names is None else tuple(dict.fromkeys(feature_names))
    wanted = None if names is None else set(names)

    fields = _compile_fields(snapshot)
    features, seen = {}, set()

    for declaration in snapshot.features:
        for name, arguments in _feature_arguments(declaration):
            # Every declared name is checked, including names this call will not run.
            if name in seen:
                raise ValueError(f"Duplicate feature: {name}")
            seen.add(name)

            # Bind a callable only for the selected names.
            if wanted is None or name in wanted:
                function = partial(declaration.function, **arguments) if arguments else declaration.function
                features[name] = CompiledFeature(declaration.source, name, function)

    # Requested names define the result order and drop anything not selected.
    if names is not None:
        features = {name: features[name] for name in names}

    # Keep fields only for sources that still have a selected feature.
    sources = dict.fromkeys(feature.source for feature in features.values())
    return FeatureCatalog(
        fields_by_source={source: fields.get(source, ()) for source in sources},
        features_by_name=features,
    )
