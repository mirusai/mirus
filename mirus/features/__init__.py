"""Lazy feature facade; direct online imports never load offline modules."""

__all__ = [
    "OfflinePlan",
    "compile_offline",
    "compile_features",
    "compute_features",
    "prepare_features",
    "FeatureCatalog",
    "feature",
    "feature_schema",
    "field",
]


def __getattr__(name):
    if name == "prepare_features":
        from .compiler import prepare_features

        return prepare_features
    if name == "FeatureCatalog":
        from .definitions import FeatureCatalog

        return FeatureCatalog
    if name in {"compile_features", "compute_features"}:
        from . import compute

        return getattr(compute, name)
    if name in {"feature", "field"}:
        from . import decorators

        return getattr(decorators, name)
    if name in {"OfflinePlan", "compile_offline", "feature_schema"}:
        from mirus import backtest

        return getattr(backtest, name)
    raise AttributeError(name)
