"""
algo_trading.strategies.base — Abstract base class and strategy registry.

Every strategy must inherit from BaseStrategy and implement:
  - generate_signals(data, vix, current_date) → list[Signal]
  - name property

Strategies can be registered with @register_strategy("name").
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Type, Dict, Any
import logging
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """
    Represents a single trade signal emitted by a strategy.

    Fields
    ------
    date        : Signal generation date (entry date)
    underlying  : Yahoo Finance ticker (e.g. "^NSEI")
    direction   : "long" (buy option) or "short" (sell option)
    option_type : "CE" or "PE"
    strike      : Strike price (0 = strategy will determine at execution)
    expiry      : Expiry date for the option
    signal_type : "entry" or "exit"
    exit_reason : Populated on exit signals — "target" | "sl" | "expiry" | "signal"
    confidence  : Strategy confidence score from 0.0 to 1.0
    meta        : Optional dict for strategy-specific metadata
    """
    date        : date
    underlying  : str
    direction   : str           # "long" | "short"
    option_type : str           # "CE" | "PE"
    strike      : float = 0.0   # 0 = ATM, will be resolved by backtester
    expiry      : Optional[date] = None
    signal_type : str = "entry" # "entry" | "exit"
    exit_reason : str = ""
    confidence  : float = 1.0
    meta        : dict = field(default_factory=dict)

    def __post_init__(self):
        if self.direction not in ("long", "short"):
            raise ValueError(f"direction must be 'long' or 'short', got '{self.direction}'")
        if self.option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be 'CE' or 'PE', got '{self.option_type}'")
        if self.signal_type not in ("entry", "exit"):
            raise ValueError(f"signal_type must be 'entry' or 'exit', got '{self.signal_type}'")
        self.confidence = self._normalize_confidence(self.confidence)
        self.meta["confidence"] = self.confidence

    @staticmethod
    def _normalize_confidence(value: float) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"confidence must be numeric, got '{value}'")
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {score}")
        return score

    def with_confidence(self, confidence: float) -> "Signal":
        """Update confidence while keeping metadata in sync."""
        self.confidence = self._normalize_confidence(confidence)
        self.meta["confidence"] = self.confidence
        return self


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement:
        name       : str property — unique strategy identifier
        generate_signals(data, vix, current_date) → list[Signal]
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name, used in report filenames."""
        ...

    @abstractmethod
    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        """
        Evaluate market conditions and return a list of trade signals.

        Parameters
        ----------
        data         : OHLCV DataFrame for this underlying, up to current_date
        vix          : Series of VIX values, same index as data
        current_date : The date being evaluated (last row of data)

        Returns
        -------
        List of Signal objects. Empty list = no action today.
        """
        ...

    def get_params(self) -> dict:
        """
        Return a dict of strategy parameters for embedding in reports.
        Override in subclass to expose strategy-specific config values.
        """
        return {"strategy": self.name}

    def score_signal(self, signal: Signal) -> float:
        """
        Return a confidence score for a signal.

        Strategies can override this to compute confidence from their own
        indicators. If they already set Signal.confidence, the default keeps it.
        """
        return signal.confidence

    def on_trade_closed(self, trade) -> None:
        """
        Optional lifecycle hook called by the backtester after a trade closes.

        Stateful directional strategies keep local position flags to avoid
        duplicate entries. This default reset keeps those flags aligned when
        the backtester exits by stop-loss, target, expiry, or forced close.
        """
        underlying = getattr(trade, "underlying", None)
        option_type = getattr(trade, "option_type", None)
        if not underlying or option_type not in ("CE", "PE"):
            return

        prev_signal = getattr(self, "_prev_signal", None)
        if isinstance(prev_signal, dict):
            expected = "long_ce" if option_type == "CE" else "long_pe"
            if prev_signal.get(underlying) == expected:
                prev_signal[underlying] = "none"

        entry_date = getattr(self, "_entry_date", None)
        if isinstance(entry_date, dict):
            entry_date[underlying] = None

        for attr in ("_pending", "_pending_dir"):
            pending = getattr(self, attr, None)
            if isinstance(pending, dict):
                pending[underlying] = ""

        for attr in ("_pending_bars", "_cross_counter"):
            counter = getattr(self, attr, None)
            if isinstance(counter, dict):
                counter[underlying] = 0


# ─────────────────────────────────────────────────────────────────
# Strategy Registry
# ─────────────────────────────────────────────────────────────────

STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {}


def register_strategy(*names: str):
    """
    Decorator to register a strategy class under one or more aliases.

    Usage:
    @register_strategy("trend", "trend_following")
    class TrendFollowingStrategy(BaseStrategy):
        ...
    """
    def decorator(cls: Type[BaseStrategy]):
        for name in names:
            key = name.strip().lower()
            STRATEGY_REGISTRY[key] = cls
        return cls
    return decorator


def get_strategy(name: str, **kwargs) -> BaseStrategy:
    """
    Factory function to instantiate a strategy by its registered name.
    """
    key = name.strip().lower()
    cls = STRATEGY_REGISTRY.get(key)
    if not cls:
        available = sorted(list(STRATEGY_REGISTRY.keys()))
        raise ValueError(
            f"Strategy '{name}' not found in registry. Available strategies: {available}"
        )
    return cls(**kwargs)


def list_strategies() -> list[str]:
    """Return all registered strategy names."""
    return sorted(list(STRATEGY_REGISTRY.keys()))
