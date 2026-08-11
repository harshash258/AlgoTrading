"""
inverse_strategy.py — Inverts the signals of any wrapped strategy.

When a strategy says BUY CE  → inverse says BUY PE
When a strategy says BUY PE  → inverse says BUY CE
When a strategy says SELL CE → inverse says SELL PE
When a strategy says SELL PE → inverse says SELL CE

Exit signals are also flipped so the position tracking stays consistent.

Why use this?
  If a strategy's win rate is below 50%, its inverse is above 50%.
  Run a backtest, check the metrics — if the strategy loses money,
  wrap it in InverseStrategy and backtest again. One of them has edge.

  Also useful for:
  - Testing whether your signals have any predictive power at all
    (a random strategy inverted should still be ~random)
  - Mean-reversion vs trend-following: if your RSI strategy works
    as a mean reversion play, its inverse is a momentum/breakout play

Usage:
    from src.strategies.inverse_strategy import InverseStrategy
    from src.strategies.combined_strategy import CombinedStrategy
    from src.strategies.trend_following import TrendFollowingStrategy

    # Invert a single strategy
    inv = InverseStrategy(TrendFollowingStrategy())

    # Invert the full combined strategy
    inv_combined = InverseStrategy(CombinedStrategy([...]))

    python main.py backtest --strategy inv_combined
    python main.py backtest --strategy inv_bb
    python main.py backtest --strategy inv_confluence
"""

import pandas as pd
from datetime import date
from copy import deepcopy

from src.strategies.base_strategy import BaseStrategy, Signal


_FLIP_OPTION = {"CE": "PE", "PE": "CE"}
_FLIP_DIR    = {"long": "short", "short": "long"}


def _flip_signal(sig: Signal) -> Signal:
    """
    Return a new Signal with option_type and direction inverted.

    Entry signals: CE↔PE, long↔short
    Exit  signals: CE↔PE (direction kept — exit must match the open position)

    The expiry and strike are preserved so the backtester can still
    price the option correctly.
    """
    new_meta = dict(sig.meta)
    new_meta["inverted"] = True
    new_meta["original_option_type"] = sig.option_type
    new_meta["original_direction"]   = sig.direction

    if sig.signal_type == "entry":
        return Signal(
            date        = sig.date,
            underlying  = sig.underlying,
            direction   = _FLIP_DIR[sig.direction],
            option_type = _FLIP_OPTION[sig.option_type],
            strike      = sig.strike,
            expiry      = sig.expiry,
            signal_type = "entry",
            meta        = new_meta,
        )
    else:
        # Exit: flip option_type so it matches the inverted open position
        return Signal(
            date        = sig.date,
            underlying  = sig.underlying,
            direction   = _FLIP_DIR[sig.direction],
            option_type = _FLIP_OPTION[sig.option_type],
            signal_type = "exit",
            exit_reason = sig.exit_reason,
            meta        = new_meta,
        )


class InverseStrategy(BaseStrategy):
    """
    Meta-wrapper that inverts all signals from a wrapped strategy.

    Parameters
    ----------
    strategy : Any BaseStrategy instance to invert
    """

    def __init__(self, strategy: BaseStrategy):
        self.strategy = strategy

    @property
    def name(self) -> str:
        return f"inv_{self.strategy.name}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        """
        Delegate to wrapped strategy, then flip every signal.
        """
        original_signals = self.strategy.generate_signals(data, vix, current_date)
        return [_flip_signal(s) for s in original_signals]

    def get_params(self) -> dict:
        params = self.strategy.get_params()
        params["strategy"]  = self.name
        params["inverted"]  = True
        params["note"]      = "All CE↔PE and long↔short signals are flipped"
        return params
