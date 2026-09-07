"""
combined_strategy.py — Runs multiple strategies simultaneously.

Aggregates signals from all child strategies on each bar.
Each strategy maintains its own independent state and position tracking.
The risk manager in the backtester enforces the global position cap.

Usage:
    strategy = CombinedStrategy([
        TrendFollowingStrategy(),
        RSIStrategy(),
    ])
"""

import pandas as pd
from datetime import date

from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy


@register_strategy("combined")
class CombinedStrategy(BaseStrategy):
    """
    Meta-strategy that wraps multiple strategies and merges their signals.

    Parameters
    ----------
    strategies : list of BaseStrategy instances
    """

    def __init__(self, strategies: list[BaseStrategy] = None):
        if strategies is None:
            from algo_trading.strategies.trend_following import TrendFollowingStrategy
            from algo_trading.strategies.rsi_strategy import RSIStrategy
            strategies = [TrendFollowingStrategy(), RSIStrategy()]
        if not strategies:
            raise ValueError("CombinedStrategy requires at least one strategy")
        self.strategies = strategies

    @property
    def name(self) -> str:
        return "combined_" + "_".join(s.name for s in self.strategies)

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        """Collect and return signals from all child strategies."""
        all_signals = []
        for strategy in self.strategies:
            try:
                sigs = strategy.generate_signals(data, vix, current_date)
                # Tag each signal with its source strategy
                for sig in sigs:
                    sig.meta["source_strategy"] = strategy.name
                all_signals.extend(sigs)
            except Exception as e:
                raise RuntimeError(f"Strategy {strategy.name} failed on {current_date}") from e
        return all_signals

    def on_trade_closed(self, trade) -> None:
        source_strategy = getattr(trade, "entry_meta", {}).get("source_strategy")
        for strategy in self.strategies:
            if source_strategy and strategy.name != source_strategy:
                continue
            strategy.on_trade_closed(trade)

    def get_params(self) -> dict:
        params = {"strategy": self.name, "num_strategies": len(self.strategies)}
        for i, s in enumerate(self.strategies):
            for k, v in s.get_params().items():
                params[f"s{i+1}_{k}"] = v
        return params
