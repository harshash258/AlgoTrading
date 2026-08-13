"""
algo_trading.reporting.metrics — Performance metrics computation and console formatting.
"""

import sys
import math
import logging
import numpy as np
import pandas as pd
from datetime import date

import config
from algo_trading.core.backtester import Trade

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
    net_pnl       = sum(net_pnls)
    total_return  = (net_pnl / starting_capital) * 100.0

    # Win rate
    total_trades = len(trades)
    win_rate     = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0

    # Profit factor
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    # Averages
    avg_trade  = np.mean(pnl_pcts)   if pnl_pcts else 0.0
    median_trade = np.median(pnl_pcts) if pnl_pcts else 0.0
    avg_win    = np.mean(wins)       if wins     else 0.0
    avg_loss   = np.mean(losses)     if losses   else 0.0
    best_trade = max(pnl_pcts)       if pnl_pcts else 0.0
    worst_trade= min(pnl_pcts)       if pnl_pcts else 0.0
    avg_held   = np.mean(held_days)  if held_days else 0.0

    # Per-trade Sharpe (annualised: multiply by sqrt(252 / avg_held_days))
    if len(pnl_pcts) > 1 and np.std(pnl_pcts) > 0:
        trades_per_year  = 252.0 / max(avg_held, 1.0)
        sharpe_per_trade = (np.mean(pnl_pcts) / np.std(pnl_pcts)) * np.sqrt(trades_per_year)
    else:
        sharpe_per_trade = 0.0

    # Sortino (downside deviation only)
    downside = [p for p in pnl_pcts if p < 0]
    if len(downside) > 1 and np.std(downside) > 0:
        trades_per_year   = 252.0 / max(avg_held, 1.0)
        sortino_per_trade = (np.mean(pnl_pcts) / np.std(downside)) * np.sqrt(trades_per_year)
    else:
        sortino_per_trade = 0.0

    # Max Drawdown & CAGR from equity curve
    max_dd = _max_drawdown(equity_curve)
    cagr   = _cagr(equity_curve, starting_capital, final_capital)

    # Average entry delta & theta
    deltas = [abs(t.entry_delta) for t in trades if t.entry_delta != 0]
    thetas = [abs(t.entry_theta) for t in trades if t.entry_theta != 0]
    avg_delta = float(np.mean(deltas)) if deltas else 0.0
    avg_theta = float(np.mean(thetas)) if thetas else 0.0

    return {
        "total_trades"      : total_trades,
        "total_wins"        : len(wins),
        "total_losses"      : len(losses),
        "win_rate_pct"      : round(win_rate, 2),
        "profit_factor"     : round(profit_factor, 3),
        "total_return_pct"  : round(total_return, 2),
        "cagr_pct"          : round(cagr, 2),
        "max_drawdown_pct"  : round(max_dd, 2),
        "avg_trade_pct"     : round(avg_trade, 2),
        "median_trade_pct"  : round(median_trade, 2),
        "avg_win_pct"       : round(avg_win, 2),
        "avg_loss_pct"      : round(avg_loss, 2),
        "best_trade_pct"    : round(best_trade, 2),
        "worst_trade_pct"   : round(worst_trade, 2),
        "avg_held_days"     : round(avg_held, 1),
        "sharpe_per_trade"  : round(sharpe_per_trade, 3),
        "sortino_per_trade" : round(sortino_per_trade, 3),
        "starting_capital"  : starting_capital,
        "final_capital"     : round(final_capital, 2),
        "net_pnl"           : round(net_pnl, 2),
        "gross_wins"        : round(gross_wins, 2),
        "gross_losses"      : round(gross_losses, 2),
        "avg_entry_delta"   : round(avg_delta, 4),
        "avg_theta_pct"     : round(avg_theta, 3),
    }


def _cagr(equity_curve: pd.DataFrame, starting_capital: float, final_capital: float) -> float:
    """Compute Compound Annual Growth Rate."""
    if equity_curve.empty or starting_capital <= 0 or final_capital <= 0:
        return 0.0

    try:
        start_date = pd.to_datetime(equity_curve.index[0])
        end_date   = pd.to_datetime(equity_curve.index[-1])
        years = (end_date - start_date).days / 365.25
        if years <= 0:
            return 0.0
        return ((final_capital / starting_capital) ** (1.0 / years) - 1.0) * 100.0
    except Exception:
        return 0.0


def _max_drawdown(equity_curve: pd.DataFrame) -> float:
    """Compute maximum drawdown percentage from equity curve."""
    if equity_curve.empty or "capital" not in equity_curve.columns:
        return 0.0

    cap = equity_curve["capital"]
    running_max = cap.cummax()
    drawdown = (running_max - cap) / running_max * 100.0
    return float(drawdown.max()) if not drawdown.empty else 0.0


def _empty_metrics(starting_capital: float) -> dict:
    """Return zeroed metrics dict when no trades occurred."""
    return {
        "total_trades": 0, "total_wins": 0, "total_losses": 0,
        "win_rate_pct": 0.0, "profit_factor": 0.0, "total_return_pct": 0.0,
        "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "avg_trade_pct": 0.0,
        "median_trade_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "best_trade_pct": 0.0, "worst_trade_pct": 0.0, "avg_held_days": 0.0,
        "sharpe_per_trade": 0.0, "sortino_per_trade": 0.0,
        "starting_capital": starting_capital, "final_capital": starting_capital,
        "net_pnl": 0.0, "gross_wins": 0.0, "gross_losses": 0.0,
        "avg_entry_delta": 0.0, "avg_theta_pct": 0.0,
    }


def trades_to_dataframe(trades: list[Trade]) -> pd.DataFrame:
    """Convert a list of Trade objects to a pandas DataFrame for CSV / reporting."""
    rows = []
    for t in trades:
        rows.append({
            "id"           : t.id,
            "underlying"   : t.underlying,
            "option_type"  : t.option_type,
            "direction"    : t.direction,
            "strike"       : t.strike,
            "expiry"       : t.expiry.isoformat() if t.expiry else "",
            "entry_date"   : t.entry_date.isoformat() if t.entry_date else "",
            "exit_date"    : t.exit_date.isoformat() if t.exit_date else "",
            "entry_premium": round(t.entry_premium, 2),
            "exit_premium" : round(t.exit_premium, 2),
            "lots"         : t.lots,
            "lot_size"     : t.lot_size,
            "entry_cost"   : round(t.entry_cost, 2),
            "exit_cost"    : round(t.exit_cost, 2),
            "exit_reason"  : t.exit_reason,
            "entry_spot"   : round(t.entry_spot, 2),
            "exit_spot"    : round(t.exit_spot, 2),
            "entry_delta"  : round(t.entry_delta, 4),
            "entry_iv"     : round(t.entry_iv, 2),
            "gross_pnl"    : round(t.gross_pnl, 2),
            "net_pnl"      : round(t.net_pnl, 2),
            "pnl_pct"      : round(t.pnl_pct, 2),
            "held_days"    : t.held_days,
        })
    return pd.DataFrame(rows)


def print_summary(metrics: dict, strategy_name: str = "") -> None:
    """Print a formatted metrics summary to console safely on all platforms."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    sep = "-" * 50
    header = f"  BACKTEST RESULTS - {strategy_name.upper()}"
    print(f"\n{'='*50}")
    print(header)
    print(f"{'='*50}")
    print(f"  Capital        : Rs. {metrics['starting_capital']:>12,.0f} -> Rs. {metrics['final_capital']:>12,.0f}")
    print(f"  Net P&L        : Rs. {metrics['net_pnl']:>+12,.0f}")
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
    print(f"  Avg Entry Delta: {metrics['avg_entry_delta']:.4f}")
    print(f"  Avg Theta %/day: {metrics['avg_theta_pct']:.3f}%")
    print(f"{'='*50}\n")
