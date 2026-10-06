"""Prepare reusable feature definitions without executing user functions."""

from collections.abc import Sequence
from functools import partial
from inspect import signature
from itertools import product

from . import registry
from .definitions import (
    CompiledFeature, CompiledField, CompiledSelection, DeclarationSnapshot,
    FeatureCatalog, FeatureDeclaration, FeatureDependency, SourceSelection,
)


def _compile_fields(snapshot: DeclarationSnapshot) -> dict[str, tuple[CompiledField, ...]]:
    grouped = {}
    seen = set()
    for declaration in snapshot.fields:
        key = (declaration.source, declaration.name)
        if key in seen:
            raise ValueError(f"Duplicate field: {key}")
        seen.add(key)
        grouped.setdefault(declaration.source, []).append(
            CompiledField(declaration.source, declaration.name, declaration.function)
        )
    return {source: tuple(fields) for source, fields in grouped.items()}


def _feature_arguments(declaration: FeatureDeclaration):
    choices = declaration.parameters
    for name, values in choices:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
            raise ValueError(f"parameter {name!r} must contain a non-empty sequence of choices")
    if choices and declaration.feature_name is None:
        raise ValueError("parameters require a feature_name template")
    if choices:
        try:
            signature(declaration.function).bind_partial(**{name: values[0] for name, values in choices})
        except TypeError as error:
            raise TypeError(
                f"{declaration.function.__name__} cannot bind declared parameters: {error}"
            ) from error

    template = declaration.feature_name or declaration.function.__name__
    names = tuple(name for name, _ in choices)
    for combination in product(*(values for _, values in choices)):
        arguments = dict(zip(names, combination))
        yield template.format(**arguments) if arguments else template, arguments


def _prepare(snapshot: DeclarationSnapshot, feature_names: Sequence[str] | None) -> FeatureCatalog:
    if isinstance(feature_names, str):
        raise TypeError("feature_names must be a sequence of names, not a string")
    names = None if feature_names is None else tuple(dict.fromkeys(feature_names))
    wanted = None if names is None else set(names)
    fields = _compile_fields(snapshot)
    features, seen = {}, set()
    for declaration in snapshot.features:
        for name, arguments in _feature_arguments(declaration):
            # Validate all names, but bind callables only for selected features.
            if name in seen:
                raise ValueError(f"Duplicate feature: {name}")
            seen.add(name)
            if wanted is None or name in wanted:
                function = partial(declaration.function, **arguments) if arguments else declaration.function
                features[name] = CompiledFeature(declaration.source, name, function)
    if names is not None:
        features = {name: features[name] for name in names}
    sources = dict.fromkeys(feature.source for feature in features.values())
    return FeatureCatalog(
        fields_by_source={source: fields.get(source, ()) for source in sources},
        features_by_name=features,
    )


def prepare_features(feature_names: Sequence[str] | None = None) -> FeatureCatalog:
    """Snapshot registered definitions; omit names to make all features available."""
    return _prepare(registry.get_default_registry().snapshot(), feature_names)


def compile_selection(
    snapshot: DeclarationSnapshot, feature_names: Sequence[str] | None = None,
) -> CompiledSelection:
    """Retain the engine-neutral metadata contract used by offline compilation."""
    catalog = _prepare(snapshot, feature_names)
    grouped = {}
    for feature in catalog.features_by_name.values():
        grouped.setdefault(feature.source, []).append(feature)
    sources = tuple(
        SourceSelection(source, catalog.fields_by_source[source], tuple(features))
        for source, features in grouped.items()
    )
    dependencies = tuple(
        FeatureDependency(feature.name, feature.source,
                          tuple(field.name for field in catalog.fields_by_source[feature.source]))
        for feature in catalog.features_by_name.values()
    )
    return CompiledSelection(tuple(catalog.features_by_name.values()), sources, dependencies)
