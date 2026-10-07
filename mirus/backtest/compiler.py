"""Compile raw declarations into a Spark-free offline feature plan."""

from collections.abc import Sequence

from mirus.features.compiler import prepare_features

from .plan import OfflinePlan


def compile_offline(
    feature_names: Sequence[str] | None = None,
) -> OfflinePlan:
    """Prepare selected callables; None selects all imported declarations."""
    features_catalog = prepare_features(feature_names)

    return OfflinePlan(catalog=features_catalog)
