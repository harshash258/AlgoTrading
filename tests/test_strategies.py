"""
tests/test_strategies.py — Test strategy registry and signal generation.
"""

from algo_trading.strategies import (
    get_strategy,
    list_strategies,
    STRATEGY_REGISTRY,
    BaseStrategy,
)


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


def test_strategy_signal_generation(sample_market_data):
    """Verify strategy runs on dataframe without exceptions."""
    df = sample_market_data["^NSEI"]
    vix = df["VIX"]
    strat = get_strategy("trend")
    
    current_date = df.index[-1].date()
    signals = strat.generate_signals(df, vix, current_date)
    assert isinstance(signals, list)
