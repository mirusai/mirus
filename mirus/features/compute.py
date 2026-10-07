"""Shared pure-Python feature execution; no retrieval or offline dependencies."""

from collections.abc import Sequence
from typing import Any

from .compiler import prepare_features
from .definitions import FeatureCatalog

__all__ = ["compute_features", "prepare_features"]


def compute_features(
    payload: dict[str, Any],
    feature_names: Sequence[str] | None = None,
    *,
    catalog: FeatureCatalog | None = None,
) -> dict[str, Any]:
    """Compute selected features, preserving one row per source record.

    Nested values are shared with the payload and must be treated as read-only.
    Fields read raw rows independently; selected features share prepared rows.
    Omit catalog to prepare the requested features per call, or all features
    when feature_names is None. A supplied catalog defines the full selection;
    feature_names is ignored and its prepared groups are executed directly.
    """
    if catalog is None:
        catalog = prepare_features(feature_names)

    # Root scalars are shared context. Objects and lists stay in their segments.
    context = {key: value for key, value in payload.items() if not isinstance(value, (dict, list))}

    result = dict.fromkeys(catalog.features_by_name)
    for source, features in catalog.features_by_source.items():
        fields = catalog.fields_by_source[source]
        value = payload.get(source, [])
        records = [value] if isinstance(value, dict) else value or []
        rows = []
        for record in records:
            row = {**context, **record}
            # Evaluate all fields against raw values before publishing their outputs.
            row.update({name: function(row) for name, function in fields.items()})
            rows.append(row)

        for name, function in features.items():
            result[name] = function(rows)
    return result
