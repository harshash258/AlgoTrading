"""
reporter.py — Computes all performance metrics from a list of closed trades.

Returns a structured dict of metrics used by both the console summary
and the HTML report generator.
"""

import math
import logging
import numpy as np
import pandas as pd
from datetime import date

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from src.backtester import Trade

logger = logging.getLogger(__name__)


def compute_metrics(
    trades: list[Trade],
    equity_curve: pd.DataFrame,
    starting_capital: float = config.STARTING_CAPITAL,
) -> dict:
    """
    Compute all 13 performance metrics from a completed backtest.

    Parameters
    ----------
    trades          : List of closed Trade objects
    equity_curve    : DataFrame with index=date, column='capital'
    starting_capital: Initial capital for return calculations

    Returns
    -------
    dict with all metrics (keys match column names in HTML report)
    """
    if not trades:
        return _empty_metrics(starting_capital)

    pnl_pcts    = [t.pnl_pct for t in trades]
    net_pnls    = [t.net_pnl for t in trades]
    held_days   = [t.held_days for t in trades]
    wins        = [p for p in pnl_pcts if p > 0]
    losses      = [p for p in pnl_pcts if p <= 0]
    gross_wins  = sum(p for p in net_pnls if p > 0)
    gross_losses= abs(sum(p for p in net_pnls if p <= 0))

    final_capital = equity_curve["capital"].iloc[-1] if not equity_curve.empty else starting_capital

    # Sharpe per trade (mean / std of trade returns)
    pnl_arr  = np.array(pnl_pcts)
    sharpe   = float(np.mean(pnl_arr) / np.std(pnl_arr)) if np.std(pnl_arr) > 0 else 0.0

    # Sortino per trade (mean / std of negative returns only — downside deviation)
    losses_arr     = np.array([p for p in pnl_pcts if p < 0])
    downside_std   = float(np.std(losses_arr)) if len(losses_arr) > 1 else 0.0
    sortino        = float(np.mean(pnl_arr) / downside_std) if downside_std > 0 else 0.0

    # CAGR — derived from equity curve date range and total return
    cagr = _cagr(equity_curve, starting_capital, final_capital)

    # Max drawdown from equity curve
    max_dd = _max_drawdown(equity_curve)

    metrics = {
        "total_trades"     : len(trades),
        "win_rate_pct"     : round(len(wins) / len(trades) * 100, 2),
        "total_return_pct" : round((final_capital - starting_capital) / starting_capital * 100, 2),
        "cagr_pct"         : round(cagr, 2),
        "avg_trade_pct"    : round(float(np.mean(pnl_pcts)), 2),
        "median_trade_pct" : round(float(np.median(pnl_pcts)), 2),
        "best_trade_pct"   : round(max(pnl_pcts), 2),
        "worst_trade_pct"  : round(min(pnl_pcts), 2),
        "avg_win_pct"      : round(float(np.mean(wins)), 2)    if wins   else 0.0,
        "avg_loss_pct"     : round(float(np.mean(losses)), 2)  if losses else 0.0,
        "profit_factor"    : round(gross_wins / gross_losses, 3) if gross_losses > 0 else float("inf"),
        "sharpe_per_trade" : round(sharpe, 3),
        "sortino_per_trade": round(sortino, 3),
        "max_drawdown_pct" : round(max_dd, 2),
        "avg_held_days"    : round(float(np.mean(held_days)), 1),
        # Extra context
        "starting_capital" : starting_capital,
        "final_capital"    : round(final_capital, 2),
        "net_pnl"          : round(final_capital - starting_capital, 2),
        "total_wins"       : len(wins),
        "total_losses"     : len(losses),
        # Greeks stats
        "avg_entry_delta"  : round(float(np.mean([abs(t.entry_delta) for t in trades])), 4),
        "avg_theta_pct"    : round(float(np.mean([
            (t.entry_theta / t.entry_premium * 100) if t.entry_premium > 0 else 0
            for t in trades
        ])), 3),
    }
    return metrics


def _cagr(equity_curve: pd.DataFrame, starting_capital: float, final_capital: float) -> float:
    """
    Compound Annual Growth Rate.
    CAGR = (final / start) ^ (1 / years) - 1
    """
    if equity_curve.empty or starting_capital <= 0 or final_capital <= 0:
        return 0.0
    try:
        start_date = pd.to_datetime(equity_curve.index[0])
        end_date   = pd.to_datetime(equity_curve.index[-1])
        years      = (end_date - start_date).days / 365.25
        if years <= 0:
            return 0.0
        return ((final_capital / starting_capital) ** (1 / years) - 1) * 100
    except Exception:
        return 0.0


def _max_drawdown(equity_curve: pd.DataFrame) -> float:
    """Calculate max drawdown % from equity curve DataFrame."""
    if equity_curve.empty:
        return 0.0
    capital = equity_curve["capital"]
    rolling_peak = capital.cummax()
    drawdown = (capital - rolling_peak) / rolling_peak * 100
    return float(abs(drawdown.min()))


def _empty_metrics(starting_capital: float) -> dict:
    return {k: 0 for k in [
        "total_trades", "win_rate_pct", "total_return_pct", "cagr_pct",
        "avg_trade_pct", "median_trade_pct", "best_trade_pct", "worst_trade_pct",
        "avg_win_pct", "avg_loss_pct", "profit_factor", "sharpe_per_trade",
        "sortino_per_trade", "max_drawdown_pct", "avg_held_days",
        "total_wins", "total_losses",
    ]} | {
        "starting_capital": starting_capital,
        "final_capital":    starting_capital,
        "net_pnl":          0,
        "avg_entry_delta":  0,
        "avg_theta_pct":    0,
    }


def trades_to_dataframe(trades: list[Trade]) -> pd.DataFrame:
    """Convert list of Trade objects to a clean DataFrame for display/export."""
    rows = []
    for i, t in enumerate(trades, 1):
        rows.append({
            "#":             i,
            "Entry Date":    t.entry_date,
            "Exit Date":     t.exit_date,
            "Underlying":    t.underlying,
            "Type":          t.option_type,
            "Direction":     t.direction.capitalize(),
            "Strike":        t.strike,
            "Expiry":        t.expiry,
            "Entry Premium": round(t.entry_premium, 2),
            "Exit Premium":  round(t.exit_premium, 2),
            "Lots":          t.lots,
            "Lot Size":      t.lot_size,
            "P&L ₹":         round(t.net_pnl, 2),
            "P&L %":         round(t.pnl_pct, 2),
            "Held Days":     t.held_days,
            "Exit Reason":   t.exit_reason,
            "Entry IV %":    round(t.entry_iv, 2),
            "Entry Delta":   round(t.entry_delta, 4),
            "Entry Theta/d": round(t.entry_theta, 4),
            "Theta % Debit": round(
                (t.entry_theta / t.entry_premium * 100) if t.entry_premium > 0 else 0, 3
            ),
        })
    return pd.DataFrame(rows)


def print_summary(metrics: dict, strategy_name: str = "") -> None:
    """Print a formatted metrics summary to console."""
    sep = "─" * 50
    print(f"\n{'═'*50}")
    print(f"  BACKTEST RESULTS — {strategy_name.upper()}")
    print(f"{'═'*50}")
    print(f"  Capital        : ₹{metrics['starting_capital']:>12,.0f} → ₹{metrics['final_capital']:>12,.0f}")
    print(f"  Net P&L        : ₹{metrics['net_pnl']:>+12,.0f}")
    print(sep)
    print(f"  Total Trades   : {metrics['total_trades']}")
    print(f"  Win Rate       : {metrics['win_rate_pct']:.2f}%  ({metrics['total_wins']}W / {metrics['total_losses']}L)")
    print(f"  Total Return   : {metrics['total_return_pct']:+.2f}%")
    print(f"  CAGR           : {metrics['cagr_pct']:+.2f}%")
    print(sep)
    print(f"  Avg Trade      : {metrics['avg_trade_pct']:+.2f}%")
    print(f"  Median Trade   : {metrics['median_trade_pct']:+.2f}%")
    print(f"  Best Trade     : {metrics['best_trade_pct']:+.2f}%")
    print(f"  Worst Trade    : {metrics['worst_trade_pct']:+.2f}%")
    print(sep)
    print(f"  Avg Win        : {metrics['avg_win_pct']:+.2f}%")
    print(f"  Avg Loss       : {metrics['avg_loss_pct']:+.2f}%")
    print(f"  Profit Factor  : {metrics['profit_factor']:.3f}")
    print(f"  Sharpe/Trade   : {metrics['sharpe_per_trade']:.3f}")
    print(f"  Sortino/Trade  : {metrics['sortino_per_trade']:.3f}")
    print(sep)
    print(f"  Max Drawdown   : {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Avg Held Days  : {metrics['avg_held_days']:.1f}")
    print(sep)
    print(f"  Avg Entry Delta: {metrics['avg_entry_delta']:.4f}")
    print(f"  Avg Theta %/day: {metrics['avg_theta_pct']:.3f}%")
    print(f"{'═'*50}\n")
