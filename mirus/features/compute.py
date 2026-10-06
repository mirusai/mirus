"""Shared pure-Python feature execution; no retrieval or offline dependencies."""

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

from .compiler import prepare_features
from .definitions import FeatureCatalog


def compute_features(
    payload: dict[str, Any],
    feature_names: Sequence[str] | None = None,
    *,
    catalog: FeatureCatalog | None = None,
) -> dict[str, Any]:
    """Prepare if needed, then compute only the requested features from this payload."""
    if isinstance(feature_names, str):
        raise TypeError("feature_names must be a sequence of names, not a string")
    if catalog is None:
        catalog = prepare_features(feature_names)
    names = catalog.features_by_name if feature_names is None else dict.fromkeys(feature_names)
    grouped = {}
    for name in names:
        feature = catalog.features_by_name[name]
        grouped.setdefault(feature.source, []).append(feature)

    context = {key: value for key, value in payload.items() if not isinstance(value, (dict, list))}
    result = {}
    for source, features in grouped.items():
        raw_rows = payload[source]
        raw_rows = [raw_rows] if isinstance(raw_rows, dict) else raw_rows or []
        fields = catalog.fields_by_source[source]
        rows = []
        for raw in raw_rows:
            row = {**context, **raw}
            row.update({field.name: field.function(row) for field in fields})
            rows.append(row)
        for feature in features:
            result[feature.name] = feature.function(rows)
    return result


def compile_features(feature_names: Sequence[str] | None = None) -> Callable:
    """Compatibility helper: return a callable with a fixed prepared selection."""
    return partial(compute_features, catalog=prepare_features(feature_names))
