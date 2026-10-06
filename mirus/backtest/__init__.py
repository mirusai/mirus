"""Optional offline retrieval and feature metadata; backends load lazily."""

from .api import compile_offline
from .interface import OfflineBackend, OfflineFetcher
from .plan import OfflinePlan
from .schema import feature_schema

__all__ = ["OfflineBackend", "OfflineFetcher", "OfflinePlan", "compile_offline", "feature_schema"]
