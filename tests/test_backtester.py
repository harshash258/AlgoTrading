"""
tests/test_backtester.py — Test backtesting execution engine.
"""

from algo_trading.core.backtester import Backtester
from algo_trading.strategies import get_strategy


def test_backtester_execution(sample_market_data):
    """Verify backtester runs over sample data without crashing."""
    strat = get_strategy("trend")
    df = sample_market_data["^NSEI"]
    start_date = df.index[0].date()
    end_date = df.index[-1].date()

    bt = Backtester(
        strategy=strat,
        data=sample_market_data,
        start=start_date,
        end=end_date,
        starting_capital=500_000,
    )

    trades = bt.run()
    assert isinstance(trades, list)
    equity_curve = bt.get_equity_curve()
    assert not equity_curve.empty
    assert "capital" in equity_curve.columns
