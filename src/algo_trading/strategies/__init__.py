"""
algo_trading.strategies package — Automatically registers all built-in strategies.
"""

from algo_trading.strategies.base import (
    BaseStrategy,
    Signal,
    register_strategy,
    get_strategy,
    list_strategies,
    STRATEGY_REGISTRY,
)

from algo_trading.strategies.trend_following import TrendFollowingStrategy
from algo_trading.strategies.rsi_strategy import RSIStrategy
from algo_trading.strategies.bollinger_band_strategy import BollingerBandStrategy
from algo_trading.strategies.confluence_strategy import ConfluenceStrategy
from algo_trading.strategies.mean_reversion import MeanReversionStrategy
from algo_trading.strategies.combined_strategy import CombinedStrategy
from algo_trading.strategies.inverse_strategy import InverseStrategy
from algo_trading.strategies.orb_strategy import ORBStrategy
from algo_trading.strategies.vwap_reversion import VWAPReversionStrategy
from algo_trading.strategies.gap_fade import GapFadeStrategy
from algo_trading.strategies.iron_condor import IronCondorStrategy
from algo_trading.strategies.long_straddle import LongStraddleStrategy
from algo_trading.strategies.vertical_spread import BullCallSpread, BearPutSpread, BullPutSpread, BearCallSpread

__all__ = [
    "BaseStrategy",
    "Signal",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "STRATEGY_REGISTRY",
    "TrendFollowingStrategy",
    "RSIStrategy",
    "BollingerBandStrategy",
    "ConfluenceStrategy",
    "MeanReversionStrategy",
    "CombinedStrategy",
    "InverseStrategy",
    "ORBStrategy",
    "VWAPReversionStrategy",
    "GapFadeStrategy",
    "IronCondorStrategy",
    "LongStraddleStrategy",
]
