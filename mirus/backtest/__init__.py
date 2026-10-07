"""Optional historical feature computation; backends load lazily."""

from .compiler import compile_offline
from .interface import Backtest
from .plan import OfflinePlan

__all__ = ["Backtest", "OfflinePlan", "compile_offline"]
