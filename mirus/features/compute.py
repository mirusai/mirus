"""Shared pure-Python feature execution; no retrieval or offline dependencies."""

from collections.abc import Sequence
from typing import Any

from .compiler import prepare_features
from .definitions import FeatureCatalog

__all__ = ["compute_features", "prepare_features"]


def _flatten(value: dict | list) -> list[dict]:
    """Flatten a source segment, or one nested record, from the deepest value.

    A list of records flattens each element. Nested objects are inlined. A
    nested list of records repeats that element once per item. Keys already on
    the element override deeper keys. Root context is applied by the caller.
    """
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return [flat for item in value for flat in _flatten(item)]
        return []

    rows = [{}]
    for key, item in value.items():
        if isinstance(item, dict) or (
            isinstance(item, list) and item and all(isinstance(element, dict) for element in item)
        ):
            rows = [{**child, **row} for row in rows for child in _flatten(item)]
        else:
            copied = list(item) if isinstance(item, list) else item
            rows = [{**row, key: copied} for row in rows]
    return rows


def compute_features(
    payload: dict[str, Any],
    feature_names: Sequence[str] | None = None,
    *,
    catalog: FeatureCatalog | None = None,
) -> dict[str, Any]:
    """Prepare if needed, then compute only the requested features from this payload."""
    # A bare string would be read one character at a time.
    if isinstance(feature_names, str):
        raise TypeError("feature_names must be a sequence of names, not a string")

    # Without a catalog, prepare this selection from the current registry.
    if catalog is None:
        catalog = prepare_features(feature_names)

    # Preserve request order, then group the selected features by source.
    names = catalog.features_by_name if feature_names is None else dict.fromkeys(feature_names)
    grouped = {}
    for name in names:
        feature = catalog.features_by_name[name]
        grouped.setdefault(feature.source, []).append(feature)

    # Root scalars are shared context. Objects and lists stay in their segments.
    context = {key: value for key, value in payload.items() if not isinstance(value, (dict, list))}

    result = {}
    for source, features in grouped.items():
        # A missing source is an empty list, so the feature decides the empty result.
        raw_rows = payload.get(source)
        if raw_rows is None:
            raw_rows = []
        fields = catalog.fields_by_source[source]

        # Flatten the whole segment, then copy root context onto every row.
        rows = []
        for flat in _flatten(raw_rows):
            row = {**context, **flat}
            # Every field for this source runs once on each flattened row.
            row.update({field.name: field.function(row) for field in fields})
            rows.append(row)

        # Selected features share those rows. Each one returns a single value.
        for feature in features:
            result[feature.name] = feature.function(rows)
    return result
