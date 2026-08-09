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

from src.strategies.base_strategy import BaseStrategy, Signal


class CombinedStrategy(BaseStrategy):
    """
    Meta-strategy that wraps multiple strategies and merges their signals.

    Parameters
    ----------
    strategies : list of BaseStrategy instances
    """

    def __init__(self, strategies: list[BaseStrategy]):
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
                import logging
                logging.getLogger(__name__).warning(
                    f"Strategy {strategy.name} error on {current_date}: {e}"
                )
        return all_signals

    def get_params(self) -> dict:
        params = {"strategy": self.name, "num_strategies": len(self.strategies)}
        for i, s in enumerate(self.strategies):
            for k, v in s.get_params().items():
                params[f"s{i+1}_{k}"] = v
        return params
