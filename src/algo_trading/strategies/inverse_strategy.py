"""
inverse_strategy.py — Inverts the signals of any wrapped strategy.

When a strategy says BUY CE  → inverse says BUY PE
When a strategy says BUY PE  → inverse says BUY CE
When a strategy says SELL CE → inverse says SELL PE
When a strategy says SELL PE → inverse says SELL CE

Exit signals are also flipped so the position tracking stays consistent.

This wrapper reverses directional CE/PE exposure while preserving buy/sell side.
Inverting a losing strategy does not guarantee profitability. Multi-leg structures
require a dedicated strategy, since swapping option types can invalidate hedges.

Usage:
    from algo_trading.strategies.inverse_strategy import InverseStrategy
    from algo_trading.strategies.combined_strategy import CombinedStrategy
    from algo_trading.strategies.trend_following import TrendFollowingStrategy

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

from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy


_FLIP_OPTION = {"CE": "PE", "PE": "CE"}
_FLIP_DIR    = {"long": "short", "short": "long"}


def _flip_signal(sig: Signal) -> Signal:
    """
    Return a new directional Signal with option_type inverted.

    Entry signals: CE↔PE, direction preserved
    Exit signals: CE↔PE, direction preserved

    The expiry and strike are preserved so the backtester can still
    price the option correctly.
    """
    if sig.structure_type != "single":
        raise ValueError("Use a dedicated spread strategy to reverse multi-leg exposure")
    new_meta = dict(sig.meta)
    new_meta["original_source_strategy"] = sig.meta.get("source_strategy")
    new_meta["inverted"] = True
    new_meta["original_option_type"] = sig.option_type
    new_meta["original_direction"]   = sig.direction

    if sig.signal_type == "entry":
        return Signal(
            date        = sig.date,
            underlying  = sig.underlying,
            direction   = sig.direction,
            option_type = _FLIP_OPTION[sig.option_type],
            strike      = sig.strike,
            expiry      = sig.expiry,
            signal_type = "entry",
            meta        = new_meta,
            execution_timing = sig.execution_timing,
        )
    else:
        # Exit: flip option_type so it matches the inverted open position
        return Signal(
            date        = sig.date,
            underlying  = sig.underlying,
            direction   = sig.direction,
            option_type = _FLIP_OPTION[sig.option_type],
            signal_type = "exit",
            exit_reason = sig.exit_reason,
            meta        = new_meta,
            execution_timing = sig.execution_timing,
        )


@register_strategy("inverse")
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

    def on_trade_closed(self, trade):
        from types import SimpleNamespace
        meta = dict(getattr(trade, "entry_meta", {}))
        source = meta.pop("original_source_strategy", None)
        meta.pop("source_strategy", None)
        if source:
            meta["source_strategy"] = source
        self.strategy.on_trade_closed(SimpleNamespace(
            underlying=trade.underlying, option_type=_FLIP_OPTION[trade.option_type], entry_meta=meta))

    def get_params(self) -> dict:
        params = self.strategy.get_params()
        params["strategy"]  = self.name
        params["inverted"]  = True
        params["note"]      = "CE/PE directional exposure is reversed; buy/sell side is preserved"
        return params
