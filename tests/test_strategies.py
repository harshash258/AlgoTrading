"""
tests/test_strategies.py — Test strategy registry and signal generation.
"""

from algo_trading.strategies import (
    get_strategy,
    list_strategies,
    STRATEGY_REGISTRY,
    BaseStrategy,
    Signal,
    CombinedStrategy,
)
from algo_trading.strategies.trend_following import TrendFollowingStrategy

import pandas as pd
import pytest
from types import SimpleNamespace


def test_strategy_registry_populated():
    """Ensure built-in strategies are registered."""
    strategies = list_strategies()
    assert "trend" in strategies
    assert "rsi" in strategies
    assert "bb" in strategies
    assert "confluence" in strategies
    assert "iron_condor" in strategies
    assert "orb" in strategies


def test_get_strategy_factory():
    """Ensure strategy instances are successfully created."""
    strat = get_strategy("trend")
    assert isinstance(strat, BaseStrategy)
    assert hasattr(strat, "generate_signals")
    assert hasattr(strat, "name")


def test_signal_validation_raises_value_error():
    """Signal validation should run even when Python assertions are optimized out."""
    with pytest.raises(ValueError, match="direction"):
        Signal(
            date=pd.Timestamp("2024-01-01").date(),
            underlying="^NSEI",
            direction="invalid",
            option_type="CE",
        )


def test_strategy_state_resets_when_trade_closes():
    strat = TrendFollowingStrategy(
        fast_ma=2,
        slow_ma=3,
        use_adx_filter=False,
        use_direction_filter=False,
        use_time_stop=False,
    )
    strat._prev_signal["^NSEI"] = "long_ce"
    strat._entry_date["^NSEI"] = pd.Timestamp("2024-01-01").date()
    strat._pending_dir["^NSEI"] = "ce"
    strat._cross_counter["^NSEI"] = 2

    strat.on_trade_closed(SimpleNamespace(underlying="^NSEI", option_type="CE"))

    assert strat._prev_signal["^NSEI"] == "none"
    assert strat._entry_date["^NSEI"] is None
    assert strat._pending_dir["^NSEI"] == ""
    assert strat._cross_counter["^NSEI"] == 0


def test_combined_strategy_routes_trade_close_to_source_strategy():
    trend = TrendFollowingStrategy(
        fast_ma=2,
        slow_ma=3,
        use_adx_filter=False,
        use_direction_filter=False,
        use_time_stop=False,
    )
    rsi = get_strategy("rsi")
    trend._prev_signal["^NSEI"] = "long_ce"
    rsi._prev_signal["^NSEI"] = "long_ce"
    combined = CombinedStrategy([trend, rsi])

    combined.on_trade_closed(SimpleNamespace(
        underlying="^NSEI",
        option_type="CE",
        entry_meta={"source_strategy": trend.name},
    ))

    assert trend._prev_signal["^NSEI"] == "none"
    assert rsi._prev_signal["^NSEI"] == "long_ce"


def test_strategy_signal_generation(sample_market_data):
    """Verify strategy runs on dataframe without exceptions."""
    df = sample_market_data["^NSEI"]
    vix = df["VIX"]
    strat = get_strategy("trend")
    
    current_date = df.index[-1].date()
    signals = strat.generate_signals(df, vix, current_date)
    assert isinstance(signals, list)


def test_trend_confirmation_can_enter_on_first_confirmed_bar():
    """A one-bar confirmation should emit on the crossover bar."""
    dates = pd.date_range("2024-01-01", periods=9, freq="B")
    close = pd.Series([10, 10, 10, 10, 10, 9, 8, 9, 11], index=dates)
    df = pd.DataFrame({
        "Open": close,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Volume": 1000,
    }, index=dates)
    df.attrs["ticker"] = "^NSEI"

    strat = TrendFollowingStrategy(
        fast_ma=2,
        slow_ma=3,
        trend_ma=3,
        adx_period=1,
        confirm_bars=1,
        use_adx_filter=False,
        use_direction_filter=False,
        use_time_stop=False,
    )

    signals = strat.generate_signals(df, pd.Series(15.0, index=dates), dates[-1].date())

    assert len(signals) == 1
    assert signals[0].signal_type == "entry"
    assert signals[0].option_type == "CE"
