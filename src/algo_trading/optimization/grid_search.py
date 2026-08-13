"""
optimizer.py — Level 1: Grid Search Parameter Optimization.

Key performance insight: stop_loss_pct and target_pct do NOT affect signal
generation — only exit timing. So we run signal generation once per
(fast_ma, slow_ma, rsi_oversold, rsi_overbought) combo, then replay those
same trades with different SL/target settings in O(trades) time.

This reduces actual backtests from 6,400 → 400 (16x speedup).

Chain data is loaded ONCE at startup and injected into backtester runs.
Config is patched in-memory per run (never written to disk during search).
"""

import os
import sys
import logging
from datetime import date
from itertools import product
from contextlib import contextmanager
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

from algo_trading.data.ingestion import get_combined_dataset, load_bhavcopy_folder, ChainLookup
from algo_trading.core.backtester import Backtester, Trade
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.strategies.trend_following import TrendFollowingStrategy
from algo_trading.strategies.rsi_strategy import RSIStrategy
from algo_trading.strategies.combined_strategy import CombinedStrategy
import algo_trading.core.backtester as _backtester_module

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Parameter grids
# ─────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────
# Config patch context manager
# ─────────────────────────────────────────────────────────────────

@contextmanager
def _patch_config(**overrides: Any):
    """Temporarily override config module attributes, then restore."""
    originals = {k: getattr(config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in originals.items():
            setattr(config, k, v)


# ─────────────────────────────────────────────────────────────────
# Build combinations — split into signal combos and risk combos
# ─────────────────────────────────────────────────────────────────

def _build_signal_combos(grid: dict) -> list[dict]:
    """Combinations that affect signal generation: MA × RSI params."""
    return [
        {"fast_ma": f, "slow_ma": s, "rsi_oversold": os_, "rsi_overbought": ob}
        for f, s, os_, ob in product(
            grid["fast_ma"], grid["slow_ma"],
            grid["rsi_oversold"], grid["rsi_overbought"],
        )
        if f < s and os_ < ob
    ]


def _build_risk_combos(grid: dict) -> list[dict]:
    """All stop_loss × target combinations (do not affect signals)."""
    return [
        {"stop_loss_pct": sl, "target_pct": tgt}
        for sl, tgt in product(grid["stop_loss_pct"], grid["target_pct"])
    ]


def _build_combinations(grid: dict) -> list[dict]:
    """Full combined grid — used for count reporting only."""
    return [
        {**s, **r}
        for s in _build_signal_combos(grid)
        for r in _build_risk_combos(grid)
    ]


# ─────────────────────────────────────────────────────────────────
# Run one signal-generation backtest
# ─────────────────────────────────────────────────────────────────

def _run_signal_backtest(
    sig_params: dict,
    market_data: dict,
    chain_lookup,
    start: date,
    end: date,
) -> tuple | None:
    """
    Run a full backtest for one signal combo.
    Returns (trades, equity_curve) or None on failure.
    SL/target are left at config defaults — we replay them separately.
    """
    try:
        with _patch_config(
            TREND_FAST_MA  = sig_params["fast_ma"],
            TREND_SLOW_MA  = sig_params["slow_ma"],
            RSI_OVERSOLD   = sig_params["rsi_oversold"],
            RSI_OVERBOUGHT = sig_params["rsi_overbought"],
        ):
            _backtester_module._chain_lookup = chain_lookup

            strategy = CombinedStrategy([
                TrendFollowingStrategy(
                    fast_ma=sig_params["fast_ma"],
                    slow_ma=sig_params["slow_ma"],
                    use_adx_filter=False,
                    use_confirm=False,
                    use_direction_filter=False,
                    use_time_stop=False,
                ),
                RSIStrategy(
                    rsi_period=config.RSI_PERIOD,
                    oversold=sig_params["rsi_oversold"],
                    overbought=sig_params["rsi_overbought"],
                    confirm_bars=config.RSI_CONFIRM_BARS,
                    time_stop_days=config.RSI_TIME_STOP_DAYS,
                    weekly=True,
                ),
            ])

            bt = Backtester(
                strategy=strategy,
                data=market_data,
                start=start,
                end=end,
                starting_capital=config.STARTING_CAPITAL,
            )
            trades = bt.run()
            equity_curve = bt.get_equity_curve()

        return trades, equity_curve

    except Exception as e:
        logger.warning(f"Signal backtest failed {sig_params}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# Replay trades with different SL/target — O(trades), not O(days)
# ─────────────────────────────────────────────────────────────────

def _apply_risk_params(
    trades: list,
    stop_loss_pct: float,
    target_pct: float,
    starting_capital: float,
) -> tuple:
    """
    Re-evaluate each trade's exit under new SL/target thresholds.
    Returns (adjusted_trades, equity_df). No strategy re-run needed.
    """
    capital = starting_capital
    adjusted: list = []
    equity_rows: list = []

    for t in trades:
        ep = t.entry_premium
        if ep <= 0:
            continue

        orig_pct = (t.exit_premium - ep) / ep * 100.0

        if t.direction == "long":
            if orig_pct <= -stop_loss_pct:
                exit_px = ep * (1 - stop_loss_pct / 100.0)
                reason = "sl"
            elif orig_pct >= target_pct:
                exit_px = ep * (1 + target_pct / 100.0)
                reason = "target"
            else:
                exit_px = t.exit_premium
                reason = t.exit_reason
        else:
            exit_px = t.exit_premium
            reason = t.exit_reason

        exit_px = max(exit_px, 0.01)

        multiplier = 1 if t.direction == "long" else -1
        gross_pnl = multiplier * (exit_px - ep) * t.lots * t.lot_size
        net_pnl   = gross_pnl - t.entry_cost - t.exit_cost
        pnl_pct   = (net_pnl / (ep * t.lots * t.lot_size)) * 100

        # Build a lightweight result object reusing the Trade dataclass
        adj = Trade(
            id=t.id, underlying=t.underlying, option_type=t.option_type,
            direction=t.direction, strike=t.strike, expiry=t.expiry,
            entry_date=t.entry_date, lots=t.lots, lot_size=t.lot_size,
            entry_premium=t.entry_premium, entry_cost=t.entry_cost,
            entry_spot=t.entry_spot, entry_delta=t.entry_delta,
            entry_iv=t.entry_iv, entry_theta=t.entry_theta,
        )
        adj.exit_date    = t.exit_date
        adj.exit_premium = round(exit_px, 4)
        adj.exit_spot    = t.exit_spot
        adj.exit_reason  = reason
        adj.exit_cost    = t.exit_cost
        adj.held_days    = t.held_days
        adj.gross_pnl    = gross_pnl
        adj.net_pnl      = net_pnl
        adj.pnl_pct      = pnl_pct

        capital += net_pnl
        adjusted.append(adj)
        if t.exit_date:
            equity_rows.append({"date": t.exit_date, "capital": capital})

    if equity_rows:
        eq = pd.DataFrame(equity_rows).set_index("date")
        eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    else:
        eq = pd.DataFrame(columns=["capital"])

    return adjusted, eq


# ─────────────────────────────────────────────────────────────────
# Progress bar
# ─────────────────────────────────────────────────────────────────

def _print_progress(current: int, total: int, best: dict | None) -> None:
    if best:
        best_str = (
            f"PF={best['profit_factor']:.3f} "
            f"(fast={best['fast_ma']}, slow={best['slow_ma']}, "
            f"os={best['rsi_oversold']}, ob={best['rsi_overbought']})"
        )
    else:
        best_str = "none yet"
    line = f"Testing {current}/{total} | Best so far: {best_str}"
    print(f"\r{line:<100}", end="", flush=True)


# ─────────────────────────────────────────────────────────────────
# Main optimization entry point
# ─────────────────────────────────────────────────────────────────

def run_optimization(
    quick: bool = False,
    start: date | None = None,
    end: date | None = None,
    tickers: list[str] | None = None,
    min_trades: int = MIN_TRADES_FILTER,
    silent: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Run the full grid-search optimization.

    Signal backtests run once per (fast_ma, slow_ma, os, ob).
    Each result is then replayed with every (stop_loss, target) variant
    in O(trades) time — no full re-run needed for risk params.
    """
    start   = start  or config.BACKTEST_START
    end     = end    or config.BACKTEST_END
    tickers = tickers or config.UNDERLYINGS

    grid          = QUICK_GRID if quick else FULL_GRID
    signal_combos = _build_signal_combos(grid)
    risk_combos   = _build_risk_combos(grid)
    total_combos  = len(signal_combos) * len(risk_combos)

    print(f"\n{'='*60}")
    print(f"  GRID SEARCH OPTIMIZATION")
    print(f"  Grid          : {'QUICK' if quick else 'FULL'}")
    print(f"  Signal combos : {len(signal_combos)}  (MA x RSI params — actual backtests)")
    print(f"  Risk variants : {len(risk_combos)}   (SL x Target — replayed, not re-run)")
    print(f"  Total results : {total_combos}")
    print(f"  Period        : {start} to {end}")
    print(f"  Tickers       : {tickers}")
    print(f"{'='*60}")

    # ── Load market data ──────────────────────────────────────────
    print("\nLoading market data...")
    market_data = get_combined_dataset(start=start, end=end)
    market_data = {k: v for k, v in market_data.items() if k in tickers}
    if not market_data:
        raise RuntimeError(f"No market data for: {tickers}")
    print(f"  Ready: {list(market_data.keys())}")

    # ── Load bhavcopy chain ONCE ──────────────────────────────────
    chain_lookup = None
    if config.BHAVCOPY_FOLDER:
        bhavcopy_path = config.BHAVCOPY_FOLDER
        if not os.path.isabs(bhavcopy_path):
            bhavcopy_path = os.path.join(os.path.dirname(__file__), "..", bhavcopy_path)
        if os.path.isdir(bhavcopy_path):
            print(f"\nLoading bhavcopy chain data (30-60s)...")
            try:
                chain_df = load_bhavcopy_folder(
                    bhavcopy_path, symbol=config.BHAVCOPY_SYMBOL,
                    pattern="fo*bhav.csv.zip",
                )
                chain_lookup = ChainLookup(chain_df)
                print(f"  Chain loaded: {len(chain_df):,} rows")
            except Exception as e:
                print(f"  WARNING: {e}. Falling back to Black-Scholes.")
        else:
            print(f"  WARNING: BHAVCOPY_FOLDER not found. Using Black-Scholes.")

    # ── Run signal combos ─────────────────────────────────────────
    print(f"\nRunning {len(signal_combos)} signal backtests + {len(risk_combos)} risk replays each...\n")

    all_results = []
    best_so_far = None
    combo_count = 0

    for sig_params in signal_combos:
        result = _run_signal_backtest(sig_params, market_data, chain_lookup, start, end)

        if result is None:
            combo_count += len(risk_combos)
            if not silent:
                _print_progress(combo_count, total_combos, best_so_far)
            continue

        base_trades, _ = result

        # Replay with each risk variant — fast O(trades) operation
        for risk_params in risk_combos:
            combo_count += 1
            if not silent:
                _print_progress(combo_count, total_combos, best_so_far)

            adj_trades, adj_equity = _apply_risk_params(
                base_trades,
                stop_loss_pct=risk_params["stop_loss_pct"],
                target_pct=risk_params["target_pct"],
                starting_capital=config.STARTING_CAPITAL,
            )

            if len(adj_trades) < min_trades:
                continue

            metrics = compute_metrics(adj_trades, adj_equity, config.STARTING_CAPITAL)

            row = {
                **sig_params, **risk_params,
                "total_trades"     : metrics["total_trades"],
                "win_rate_pct"     : metrics["win_rate_pct"],
                "total_return_pct" : metrics["total_return_pct"],
                "cagr_pct"         : metrics["cagr_pct"],
                "profit_factor"    : metrics["profit_factor"],
                "sharpe_per_trade" : metrics["sharpe_per_trade"],
                "sortino_per_trade": metrics["sortino_per_trade"],
                "max_drawdown_pct" : metrics["max_drawdown_pct"],
                "avg_held_days"    : metrics["avg_held_days"],
            }
            all_results.append(row)

            if best_so_far is None or (
                row["profit_factor"] > best_so_far["profit_factor"]
                or (
                    row["profit_factor"] == best_so_far["profit_factor"]
                    and row["sharpe_per_trade"] > best_so_far["sharpe_per_trade"]
                )
            ):
                best_so_far = row

    print()  # newline after progress line

    if not all_results:
        print("\nNo results passed the minimum trades filter.")
        return pd.DataFrame(columns=RESULT_COLUMNS), {}

    # ── Sort and rank ─────────────────────────────────────────────
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(
        by=["profit_factor", "sharpe_per_trade"], ascending=[False, False]
    ).reset_index(drop=True)
    results_df.insert(0, "rank", results_df.index + 1)
    for col in RESULT_COLUMNS:
        if col not in results_df.columns:
            results_df[col] = None
    results_df = results_df[RESULT_COLUMNS]

    # ── Save CSV ──────────────────────────────────────────────────
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    csv_path = os.path.join(config.REPORTS_DIR, f"optimization_results_{date.today().isoformat()}.csv")
    results_df.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"\nResults saved: {csv_path}")

    _print_results_table(results_df.head(10))
    best_params = results_df.iloc[0].to_dict()
    _apply_best_params_to_config(best_params)

    return results_df, best_params


# ─────────────────────────────────────────────────────────────────
# Print ranked results table
# ─────────────────────────────────────────────────────────────────

def _print_results_table(df: pd.DataFrame) -> None:
    sep = "-" * 110
    header = (
        f"{'Rank':>4} | {'fMA':>4} | {'sMA':>4} | {'OS':>3} | {'OB':>3} | "
        f"{'SL%':>4} | {'Tgt%':>5} | {'Trades':>6} | {'WR%':>6} | "
        f"{'Ret%':>7} | {'CAGR%':>6} | {'PF':>6} | {'Sharpe':>7} | {'DD%':>6}"
    )
    print(f"\n{'='*110}")
    print("  TOP OPTIMIZATION RESULTS")
    print(f"{'='*110}")
    print(header)
    print(sep)
    for _, row in df.iterrows():
        pf_str = f"{row['profit_factor']:.3f}" if row["profit_factor"] != float("inf") else "Inf"
        print(
            f"{int(row['rank']):>4} | {int(row['fast_ma']):>4} | {int(row['slow_ma']):>4} | "
            f"{int(row['rsi_oversold']):>3} | {int(row['rsi_overbought']):>3} | "
            f"{int(row['stop_loss_pct']):>4} | {int(row['target_pct']):>5} | "
            f"{int(row['total_trades']):>6} | {row['win_rate_pct']:>6.2f} | "
            f"{row['total_return_pct']:>+7.2f} | {row['cagr_pct']:>+6.2f} | "
            f"{pf_str:>6} | {row['sharpe_per_trade']:>7.3f} | "
            f"{row['max_drawdown_pct']:>6.2f}"
        )
    print(sep)


# ─────────────────────────────────────────────────────────────────
# Apply best params to config.py
# ─────────────────────────────────────────────────────────────────

def _apply_best_params_to_config(best: dict) -> None:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        original_content = f.read()

    lines = original_content.splitlines()
    new_lines = list(lines)

    updates = {
        "TREND_FAST_MA"    : int(best["fast_ma"]),
        "TREND_SLOW_MA"    : int(best["slow_ma"]),
        "RSI_OVERSOLD"     : int(best["rsi_oversold"]),
        "RSI_OVERBOUGHT"   : int(best["rsi_overbought"]),
        "BUY_STOP_LOSS_PCT": float(best["stop_loss_pct"]),
        "BUY_TARGET_PCT"   : float(best["target_pct"]),
    }

    changes = []
    for var_name, new_val in updates.items():
        for idx, line in enumerate(new_lines):
            if line.strip().startswith(var_name) and "=" in line:
                old_line = new_lines[idx]
                parts = line.split("=", 1)
                lhs = parts[0]
                rhs = parts[1].strip()
                comment = ("  " + rhs[rhs.index("#"):]) if "#" in rhs else ""
                val_str = f"{new_val:.1f}" if isinstance(new_val, float) and new_val == int(new_val) else str(new_val)
                new_line = f"{lhs}= {val_str}{comment}"
                if new_line != old_line:
                    changes.append((var_name, old_line.strip(), new_line.strip()))
                new_lines[idx] = new_line
                break

    if not changes:
        print("\nConfig.py already matches best parameters.")
        return

    new_content = "\n".join(new_lines)
    if original_content.endswith("\n"):
        new_content += "\n"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"\n{'='*60}")
    print("  CONFIG.PY UPDATED — Best parameters applied:")
    print(f"{'='*60}")
    for var_name, old_line, new_line in changes:
        print(f"  {var_name}:")
        print(f"    BEFORE: {old_line}")
        print(f"    AFTER : {new_line}")
    print(f"{'='*60}")
    print(f"  Saved to: {os.path.abspath(config_path)}")


# ─────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    results_df, best = run_optimization()
    print(f"\nBest params: {best}")
