"""mirus: shared feature APIs with optional serving and backtest components."""

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

__version__ = "0.1.0"


def __getattr__(name):
    if name in __all__:
        from . import features

        return getattr(features, name)
    raise AttributeError(name)
