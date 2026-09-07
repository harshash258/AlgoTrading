"""Full execution grid search. Results never mutate trading configuration."""
from datetime import date
from itertools import product
from pathlib import Path
import pandas as pd
import config
from algo_trading.core.backtester import Backtester
from algo_trading.data.ingestion import get_combined_dataset
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.strategies.trend_following import TrendFollowingStrategy
from algo_trading.strategies.rsi_strategy import RSIStrategy
from algo_trading.strategies.combined_strategy import CombinedStrategy

FULL_GRID = {
    "fast_ma"       : [5, 10, 15, 20, 25],
    "slow_ma"       : [30, 40, 50, 60, 75],
    "rsi_oversold"  : [25, 30, 35, 40],
    "rsi_overbought": [60, 65, 70, 75],
    "stop_loss_pct" : [40, 50, 60, 70],
    "target_pct"    : [75, 100, 150, 200],
}

QUICK_GRID = {
    "fast_ma"       : [10, 20, 25],
    "slow_ma"       : [40, 50, 75],
    "rsi_oversold"  : [30, 35],
    "rsi_overbought": [65, 70],
    "stop_loss_pct" : [50, 60],
    "target_pct"    : [100, 150],
}

MIN_TRADES_FILTER = 10

RESULT_COLUMNS = [
    "rank", "fast_ma", "slow_ma", "rsi_oversold", "rsi_overbought",
    "stop_loss_pct", "target_pct", "total_trades", "win_rate_pct",
    "total_return_pct", "cagr_pct", "profit_factor", "sharpe_per_trade",
    "sortino_per_trade", "max_drawdown_pct", "avg_held_days",
]



def _build_combinations(grid):
    keys = list(grid)
    return [p for values in product(*(grid[k] for k in keys))
            if (p := dict(zip(keys, values)))["fast_ma"] < p["slow_ma"]
            and p["rsi_oversold"] < p["rsi_overbought"]]


def _run_signal_backtest(params, market_data, chain_lookup, start, end, **engine_options):
    """Fresh strategy and execution state for every complete parameter combination."""
    strategy = CombinedStrategy([
        TrendFollowingStrategy(fast_ma=int(params["fast_ma"]), slow_ma=int(params["slow_ma"]),
                               use_adx_filter=False, use_confirm=False,
                               use_direction_filter=False, use_time_stop=False),
        RSIStrategy(rsi_period=config.RSI_PERIOD, oversold=float(params["rsi_oversold"]),
                    overbought=float(params["rsi_overbought"]), confirm_bars=config.RSI_CONFIRM_BARS,
                    time_stop_days=config.RSI_TIME_STOP_DAYS, weekly=True),
    ])
    if chain_lookup is not None and not isinstance(chain_lookup, dict):
        if len(market_data) != 1:
            raise ValueError("Multi-underlying runs require per-underlying chain lookups")
        chain_lookup = {next(iter(market_data)): chain_lookup}
    bt = Backtester(strategy, market_data, start, end, config.STARTING_CAPITAL,
                    chain_lookups=chain_lookup,
                    risk_overrides={"BUY_STOP_LOSS_PCT": float(params["stop_loss_pct"]),
                                    "BUY_TARGET_PCT": float(params["target_pct"])}, **engine_options)
    return bt.run(), bt.get_equity_curve()


def search_parameters(candidates, market_data, start, end, min_trades=10, chain_lookup=None, **engine_options):
    rows = []
    for params in candidates:
        trades, equity = _run_signal_backtest(params, market_data, chain_lookup, start, end, **engine_options)
        metrics = compute_metrics(trades, equity, config.STARTING_CAPITAL)
        if metrics["total_trades"] >= min_trades:
            rows.append({**params, **metrics})
    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    result = pd.DataFrame(rows).sort_values(["profit_factor", "sharpe_ratio"], ascending=False).reset_index(drop=True)
    result.insert(0, "rank", range(1, len(result) + 1))
    return result


def run_optimization(quick=False, start=None, end=None, tickers=None, min_trades=10,
                     silent=False, market_data=None, grid=None, **engine_options):
    start, end = start or config.BACKTEST_START, end or config.BACKTEST_END
    tickers = tickers or config.UNDERLYINGS
    if market_data is None:
        market_data = get_combined_dataset(start=start, end=end)
    market_data = {k: v for k, v in market_data.items() if k in tickers}
    if not market_data:
        raise ValueError("No market data")
    candidates = _build_combinations(grid or (QUICK_GRID if quick else FULL_GRID))
    if not silent:
        print(f"Running {len(candidates)} full execution backtests; configuration is unchanged.")
    results = search_parameters(candidates, market_data, start, end, min_trades, **engine_options)
    Path(config.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    results.to_csv(Path(config.REPORTS_DIR) / f"optimization_results_{date.today()}.csv", index=False)
    return results, results.iloc[0].to_dict() if not results.empty else {}
