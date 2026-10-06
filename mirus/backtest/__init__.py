"""Optional offline retrieval and feature metadata; backends load lazily."""

from .compiler import compile_offline
from .interface import OfflineBackend, OfflineFetcher
from .plan import OfflinePlan

__all__ = ["OfflineBackend", "OfflineFetcher", "OfflinePlan", "compile_offline"]
