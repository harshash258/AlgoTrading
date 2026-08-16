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

    def __init__(
        self,
        strategies: list[BaseStrategy] = None,
        min_confidence: float = 0.0,
        rank_entries: bool = True,
    ):
        if strategies is None:
            from algo_trading.strategies.trend_following import TrendFollowingStrategy
            from algo_trading.strategies.rsi_strategy import RSIStrategy
            strategies = [TrendFollowingStrategy(), RSIStrategy()]
        if not strategies:
            raise ValueError("CombinedStrategy requires at least one strategy")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        self.strategies = strategies
        self.min_confidence = min_confidence
        self.rank_entries = rank_entries

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
                for sig in sigs:
                    confidence = strategy.score_signal(sig)
                    sig.with_confidence(confidence)
                    sig.meta["source_strategy"] = strategy.name
                    if sig.signal_type == "entry" and sig.confidence < self.min_confidence:
                        continue
                    all_signals.append(sig)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Strategy {strategy.name} error on {current_date}: {e}"
                )
        if self.rank_entries:
            all_signals.sort(key=lambda sig: (sig.signal_type != "exit", -sig.confidence))
        return all_signals

    def on_trade_closed(self, trade) -> None:
        source_strategy = getattr(trade, "entry_meta", {}).get("source_strategy")
        for strategy in self.strategies:
            if source_strategy and strategy.name != source_strategy:
                continue
            strategy.on_trade_closed(trade)

    def get_params(self) -> dict:
        params = {
            "strategy": self.name,
            "num_strategies": len(self.strategies),
            "min_confidence": self.min_confidence,
            "rank_entries": self.rank_entries,
        }
        for i, s in enumerate(self.strategies):
            for k, v in s.get_params().items():
                params[f"s{i+1}_{k}"] = v
        return params
