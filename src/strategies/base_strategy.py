"""
base_strategy.py — Abstract base class for all trading strategies.

Every strategy must inherit from BaseStrategy and implement:
  - generate_signals(data, vix) → list of Signal dicts
  - name property

The backtester calls generate_signals() on each bar and collects
the returned signals for execution.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
import pandas as pd


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
    meta        : dict = field(default_factory=dict)

    def __post_init__(self):
        assert self.direction in ("long", "short"), \
            f"direction must be 'long' or 'short', got '{self.direction}'"
        assert self.option_type in ("CE", "PE"), \
            f"option_type must be 'CE' or 'PE', got '{self.option_type}'"
        assert self.signal_type in ("entry", "exit"), \
            f"signal_type must be 'entry' or 'exit', got '{self.signal_type}'"


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement:
        name       : str property — unique strategy identifier
        generate_signals(data, vix) → list[Signal]
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
