"""Prepare reusable feature definitions without executing user functions."""

from collections.abc import Sequence
from functools import partial
from itertools import product

from . import registry
from .definitions import FeatureCatalog, FeatureDeclaration


def _group_by_source(features):
    """Group (name, (source, callable)) entries into ready-to-execute functions."""
    grouped = {}
    for name, (source, function) in features:
        grouped.setdefault(source, {})[name] = function
    return grouped


def _feature_arguments(declaration: FeatureDeclaration):
    """Expand one declaration into output names and bound parameter values.

    A declaration without parameters yields its function name once. A
    parameterized declaration yields one name per combination of choices.
    """
    choices = declaration.parameters

    template = declaration.feature_name or declaration.function.__name__
    names = tuple(name for name, _ in choices)

    # No choices still yield one empty combination, which keeps the single output name.
    for combination in product(*(values for _, values in choices)):
        arguments = dict(zip(names, combination))
        yield template.format(**arguments) if arguments else template, arguments


def prepare_features(feature_names: Sequence[str] | None = None) -> FeatureCatalog:
    """Snapshot the process registry and prepare that copy.

    Pass feature names to prepare only those outputs. Omit them to prepare
    every registered feature. Group selected functions by source, then attach
    their row transforms without calling user functions. Run project validation
    in development/CI before deploying these definitions.
    """
    if isinstance(feature_names, str):
        raise TypeError("feature_names must be a sequence of names, not a string")

    field_declarations, feature_declarations = registry.default_registry.snapshot()

    names = None if feature_names is None else tuple(dict.fromkeys(feature_names))
    wanted = None if names is None else set(names)

    features = {}

    for declaration in feature_declarations:
        for name, arguments in _feature_arguments(declaration):
            # Bind a callable only for the selected names.
            if wanted is None or name in wanted:
                function = partial(declaration.function, **arguments) if arguments else declaration.function
                features[name] = (declaration.source, function)

    if names is not None:
        features = {name: features[name] for name in names}

    grouped = _group_by_source(features.items())

    fields = {source: {} for source in grouped}
    for declaration in field_declarations:
        if declaration.source in fields:
            fields[declaration.source][declaration.name] = declaration.function

    return FeatureCatalog(
        fields_by_source=fields,
        features_by_source=grouped,
        features_by_name=features,
    )
