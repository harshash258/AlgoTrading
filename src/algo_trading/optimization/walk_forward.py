"""
walk_forward.py — Level 2: Walk-Forward Validation.

Validates the top N parameter sets from Level 1 against unseen out-of-sample
periods using a rolling train/test window.

Walk-forward config (defaults):
  Train window : 3 years
  Test window  : 1 year
  Step         : 1 year (rolling)

For each parameter set, runs every fold and reports:
  - Per-fold test metrics (PF, return%, win_rate, max_dd)
  - Mean and std of test-period profit_factor across all folds
  - Consistency score: % of folds where PF > 1.0
  - Overfitting flag: in-sample PF >> out-of-sample PF by > 50%

Output saved to: reports/walk_forward_results_YYYY-MM-DD.csv

Usage
-----
  from algo_trading.optimization.walk_forward import run_walk_forward
  wf_df = run_walk_forward(top_params_list)
"""

import os
import sys
import logging
from datetime import date
from dateutil.relativedelta import relativedelta
from typing import Any
from contextlib import contextmanager

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

from algo_trading.data.ingestion import get_combined_dataset, load_bhavcopy_folder, ChainLookup
from algo_trading.core.backtester import Backtester
from algo_trading.core.regime import classify_regime
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.strategies.trend_following import TrendFollowingStrategy
from algo_trading.strategies.rsi_strategy import RSIStrategy
from algo_trading.strategies.combined_strategy import CombinedStrategy
import algo_trading.core.backtester as _backtester_module

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Config patch context manager (shared with optimizer)
# ─────────────────────────────────────────────────────────────────

@contextmanager
def _patch_config(**overrides: Any):
    """Temporarily override config module attributes, then restore."""
    originals = {}
    for key, val in overrides.items():
        originals[key] = getattr(config, key)
        setattr(config, key, val)
    try:
        yield
    finally:
        for key, val in originals.items():
            setattr(config, key, val)


# ─────────────────────────────────────────────────────────────────
# Fold generation
# ─────────────────────────────────────────────────────────────────

def generate_folds(
    full_start: date,
    full_end: date,
    train_years: int = 3,
    test_years: int = 1,
    step_years: int = 1,
) -> list[dict]:
    """
    Generate rolling train/test fold windows.

    Example for 2015-2026, train=3yr, test=1yr, step=1yr:
      Fold 1: Train 2015-2017, Test 2018
      Fold 2: Train 2016-2018, Test 2019
      ...
      Fold 7: Train 2021-2023, Test 2024

    Returns list of dicts with keys:
      fold_num, train_start, train_end, test_start, test_end
    """
    folds = []
    fold_num = 1

    train_start = full_start
    while True:
        train_end = train_start + relativedelta(years=train_years) - relativedelta(days=1)
        test_start = train_end + relativedelta(days=1)
        test_end = test_start + relativedelta(years=test_years) - relativedelta(days=1)

        # Stop if test period extends beyond the full data range
        if test_end > full_end:
            break

        folds.append({
            "fold_num"   : fold_num,
            "train_start": train_start,
            "train_end"  : train_end,
            "test_start" : test_start,
            "test_end"   : test_end,
        })

        train_start = train_start + relativedelta(years=step_years)
        fold_num += 1

    return folds


# ─────────────────────────────────────────────────────────────────
# Run one backtest period with given params
# ─────────────────────────────────────────────────────────────────

def _run_period(
    params: dict,
    market_data: dict,
    chain_lookup: ChainLookup | None,
    start: date,
    end: date,
) -> dict:
    """
    Run a single backtest for the given parameter set and date window.
    Returns a metrics dict. Returns empty metrics on failure.
    """
    try:
        # Filter market_data to the period window
        period_data = {}
        for ticker, df in market_data.items():
            mask = (df.index.date >= start) & (df.index.date <= end)
            sliced = df.loc[mask].copy()
            if not sliced.empty:
                period_data[ticker] = sliced

        if not period_data:
            return {"total_trades": 0, "profit_factor": 0.0, "total_return_pct": 0.0,
                    "win_rate_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_per_trade": 0.0}

        with _patch_config(
            TREND_FAST_MA     = int(params["fast_ma"]),
            TREND_SLOW_MA     = int(params["slow_ma"]),
            RSI_OVERSOLD      = float(params["rsi_oversold"]),
            RSI_OVERBOUGHT    = float(params["rsi_overbought"]),
            BUY_STOP_LOSS_PCT = float(params["stop_loss_pct"]),
            BUY_TARGET_PCT    = float(params["target_pct"]),
        ):
            _backtester_module._chain_lookup = chain_lookup

            trend_strat = TrendFollowingStrategy(
                fast_ma              = int(params["fast_ma"]),
                slow_ma              = int(params["slow_ma"]),
                use_adx_filter       = False,
                use_confirm          = False,
                use_direction_filter = False,
                use_time_stop        = False,
            )
            rsi_strat = RSIStrategy(
                rsi_period     = config.RSI_PERIOD,
                oversold       = float(params["rsi_oversold"]),
                overbought     = float(params["rsi_overbought"]),
                confirm_bars   = config.RSI_CONFIRM_BARS,
                time_stop_days = config.RSI_TIME_STOP_DAYS,
                weekly         = True,
            )
            strategy = CombinedStrategy([trend_strat, rsi_strat])

            bt = Backtester(
                strategy         = strategy,
                data             = period_data,
                start            = start,
                end              = end,
                starting_capital = config.STARTING_CAPITAL,
            )
            trades = bt.run()
            equity_curve = bt.get_equity_curve()

        metrics = compute_metrics(trades, equity_curve, config.STARTING_CAPITAL)
        first_df = next(iter(period_data.values()))
        metrics["regime"] = classify_regime(first_df).primary
        return metrics

    except Exception as e:
        logger.warning(f"Walk-forward run failed ({start}→{end}): {e}")
        return {"total_trades": 0, "profit_factor": 0.0, "total_return_pct": 0.0,
                "win_rate_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_per_trade": 0.0}


# ─────────────────────────────────────────────────────────────────
# Walk-forward for a single parameter set
# ─────────────────────────────────────────────────────────────────

def _walk_forward_single(
    params: dict,
    folds: list[dict],
    market_data: dict,
    chain_lookup: ChainLookup | None,
    param_idx: int,
    total_params: int,
) -> tuple[list[dict], dict]:
    """
    Run walk-forward validation for one parameter set across all folds.

    Returns:
      (fold_rows, summary)
        fold_rows : list of per-fold result dicts
        summary   : aggregated summary dict
    """
    param_label = (
        f"fast={int(params['fast_ma'])}, slow={int(params['slow_ma'])}, "
        f"os={int(params['rsi_oversold'])}, ob={int(params['rsi_overbought'])}, "
        f"sl={int(params['stop_loss_pct'])}, tgt={int(params['target_pct'])}"
    )
    print(f"\n  Param set {param_idx}/{total_params}: [{param_label}]")

    fold_rows = []
    in_sample_pfs = []
    out_of_sample_pfs = []

    for fold in folds:
        print(
            f"    Fold {fold['fold_num']:>2}: "
            f"Train {fold['train_start']} - {fold['train_end']} | "
            f"Test  {fold['test_start']} - {fold['test_end']}",
            end=" ... ",
            flush=True,
        )

        # In-sample run (train period)
        train_metrics = _run_period(
            params, market_data, chain_lookup,
            fold["train_start"], fold["train_end"],
        )
        # Out-of-sample run (test period)
        test_metrics = _run_period(
            params, market_data, chain_lookup,
            fold["test_start"], fold["test_end"],
        )

        train_pf = train_metrics.get("profit_factor", 0.0)
        test_pf  = test_metrics.get("profit_factor", 0.0)

        # Cap inf profit factors for display and calculation
        if train_pf == float("inf"):
            train_pf = 9.999
        if test_pf == float("inf"):
            test_pf = 9.999

        in_sample_pfs.append(train_pf)
        out_of_sample_pfs.append(test_pf)

        row = {
            "param_set"      : param_label,
            "fold"           : fold["fold_num"],
            "train_start"    : fold["train_start"].isoformat(),
            "train_end"      : fold["train_end"].isoformat(),
            "test_start"     : fold["test_start"].isoformat(),
            "test_end"       : fold["test_end"].isoformat(),
            "train_pf"       : round(train_pf, 3),
            "train_trades"   : train_metrics.get("total_trades", 0),
            "test_pf"        : round(test_pf, 3),
            "test_return_pct": round(test_metrics.get("total_return_pct", 0.0), 2),
            "test_win_rate"  : round(test_metrics.get("win_rate_pct", 0.0), 2),
            "test_max_dd"    : round(test_metrics.get("max_drawdown_pct", 0.0), 2),
            "test_trades"    : test_metrics.get("total_trades", 0),
            "regime"         : test_metrics.get("regime", "unknown"),
            "fast_ma"        : int(params["fast_ma"]),
            "slow_ma"        : int(params["slow_ma"]),
            "rsi_oversold"   : int(params["rsi_oversold"]),
            "rsi_overbought" : int(params["rsi_overbought"]),
            "stop_loss_pct"  : float(params["stop_loss_pct"]),
            "target_pct"     : float(params["target_pct"]),
        }
        fold_rows.append(row)

        print(
            f"Train PF={train_pf:.3f} ({train_metrics.get('total_trades',0)} trades) | "
            f"Test PF={test_pf:.3f} ({test_metrics.get('total_trades',0)} trades, "
            f"Ret={test_metrics.get('total_return_pct',0.0):+.2f}%)"
        )

    # ── Summary calculations ──────────────────────────────────────
    oos_pf_arr = np.array(out_of_sample_pfs)
    is_pf_arr  = np.array(in_sample_pfs)

    mean_oos_pf   = float(np.mean(oos_pf_arr))
    std_oos_pf    = float(np.std(oos_pf_arr))
    mean_is_pf    = float(np.mean(is_pf_arr))

    n_profitable  = int(np.sum(oos_pf_arr > 1.0))
    total_folds   = len(folds)
    consistency   = round(n_profitable / total_folds * 100, 1) if total_folds > 0 else 0.0

    # Overfitting flag: in-sample PF > out-of-sample PF by more than 50%
    overfit_flag = (
        mean_is_pf > 0
        and mean_oos_pf > 0
        and (mean_is_pf - mean_oos_pf) / mean_is_pf > 0.50
    )

    summary = {
        "param_set"       : param_label,
        "fast_ma"         : int(params["fast_ma"]),
        "slow_ma"         : int(params["slow_ma"]),
        "rsi_oversold"    : int(params["rsi_oversold"]),
        "rsi_overbought"  : int(params["rsi_overbought"]),
        "stop_loss_pct"   : float(params["stop_loss_pct"]),
        "target_pct"      : float(params["target_pct"]),
        "total_folds"     : total_folds,
        "profitable_folds": n_profitable,
        "consistency_pct" : consistency,
        "mean_oos_pf"     : round(mean_oos_pf, 3),
        "std_oos_pf"      : round(std_oos_pf, 3),
        "mean_is_pf"      : round(mean_is_pf, 3),
        "overfit_flag"    : overfit_flag,
        "total_test_trades": int(sum(row["test_trades"] for row in fold_rows)),
    }

    # Print fold summary
    print(f"\n    Summary for [{param_label}]:")
    print(f"      OOS PF: mean={mean_oos_pf:.3f}, std={std_oos_pf:.3f}")
    print(f"      IS  PF: mean={mean_is_pf:.3f}")
    print(f"      Consistency: {n_profitable}/{total_folds} folds profitable ({consistency:.1f}%)")
    if overfit_flag:
        print(f"      [!] OVERFIT FLAG: IS PF ({mean_is_pf:.3f}) >> OOS PF ({mean_oos_pf:.3f}) by >50%")

    return fold_rows, summary


# ─────────────────────────────────────────────────────────────────
# Main walk-forward entry point
# ─────────────────────────────────────────────────────────────────

def run_walk_forward(
    top_params: list[dict],
    train_years: int = 3,
    test_years: int = 1,
    step_years: int = 1,
    start: date | None = None,
    end: date | None = None,
    tickers: list[str] | None = None,
    chain_lookup: ChainLookup | None = None,
    market_data: dict | None = None,
) -> pd.DataFrame:
    """
    Run walk-forward validation on a list of parameter sets.

    Parameters
    ----------
    top_params    : List of param dicts from the optimizer
    train_years   : Training window in years (default 3)
    test_years    : Test window in years (default 1)
    step_years    : Roll step in years (default 1)
    start         : Full data start (default: config.BACKTEST_START)
    end           : Full data end (default: config.BACKTEST_END)
    tickers       : Underlyings to test (default: config.UNDERLYINGS)
    chain_lookup  : Pre-loaded ChainLookup (loaded here if None)
    market_data   : Pre-loaded market data (loaded here if None)

    Returns
    -------
    DataFrame of per-fold results for all parameter sets
    """
    start   = start   or config.BACKTEST_START
    end     = end     or config.BACKTEST_END
    tickers = tickers or config.UNDERLYINGS

    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD VALIDATION")
    print(f"  Parameter sets : {len(top_params)}")
    print(f"  Train window   : {train_years} year(s)")
    print(f"  Test window    : {test_years} year(s)")
    print(f"  Step           : {step_years} year(s)")
    print(f"  Full period    : {start} to {end}")
    print(f"{'='*60}")

    # ── Load market data if not provided ─────────────────────────
    if market_data is None:
        print("\nLoading market data...")
        market_data = get_combined_dataset(start=start, end=end)
        market_data = {k: v for k, v in market_data.items() if k in tickers}

    # ── Load bhavcopy chain if not provided ──────────────────────
    if chain_lookup is None and config.BHAVCOPY_FOLDER:
        bhavcopy_path = config.BHAVCOPY_FOLDER
        if not os.path.isabs(bhavcopy_path):
            bhavcopy_path = os.path.join(
                os.path.dirname(__file__), "..", bhavcopy_path
            )
        if os.path.isdir(bhavcopy_path):
            print("\nLoading bhavcopy chain data...")
            try:
                chain_df = load_bhavcopy_folder(
                    bhavcopy_path,
                    symbol=config.BHAVCOPY_SYMBOL,
                    pattern="fo*bhav.csv.zip",
                )
                chain_lookup = ChainLookup(chain_df)
                print(f"  Chain data loaded: {len(chain_df):,} rows")
            except Exception as e:
                print(f"  WARNING: Failed to load bhavcopy: {e}. Using Black-Scholes.")

    # ── Generate folds ────────────────────────────────────────────
    folds = generate_folds(start, end, train_years, test_years, step_years)
    if not folds:
        print(f"\nERROR: No folds generated. Data range {start}→{end} may be too short.")
        return pd.DataFrame()

    print(f"\nGenerated {len(folds)} folds:")
    for fold in folds:
        print(
            f"  Fold {fold['fold_num']}: "
            f"Train {fold['train_start']} - {fold['train_end']} | "
            f"Test  {fold['test_start']} - {fold['test_end']}"
        )

    # ── Run walk-forward for each param set ───────────────────────
    all_fold_rows = []
    all_summaries = []

    for i, params in enumerate(top_params, 1):
        fold_rows, summary = _walk_forward_single(
            params, folds, market_data, chain_lookup,
            param_idx=i, total_params=len(top_params),
        )
        all_fold_rows.extend(fold_rows)
        all_summaries.append(summary)

    # ── Save fold-level CSV ───────────────────────────────────────
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    csv_filename = f"walk_forward_results_{date.today().isoformat()}.csv"
    csv_path = os.path.join(config.REPORTS_DIR, csv_filename)

    results_df = pd.DataFrame(all_fold_rows)
    results_df.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"\n\nWalk-forward results saved: {csv_path}")

    # ── Print summary table ───────────────────────────────────────
    _print_wf_summary(all_summaries)
    _print_regime_summary(results_df)

    # ── Print recommendation ──────────────────────────────────────
    _print_recommendation(all_summaries)

    return results_df


# ─────────────────────────────────────────────────────────────────
# Print walk-forward summary table
# ─────────────────────────────────────────────────────────────────

def _print_wf_summary(summaries: list[dict]) -> None:
    sep = "-" * 100
    print(f"\n{'='*100}")
    print("  WALK-FORWARD SUMMARY")
    print(f"{'='*100}")
    header = (
        f"{'#':>2} | {'fMA':>4} | {'sMA':>4} | {'OS':>3} | {'OB':>3} | "
        f"{'SL%':>4} | {'Tgt%':>5} | {'Folds':>5} | {'Profit':>6} | "
        f"{'Consist%':>8} | {'OOS PF':>6} | {'OOS std':>7} | "
        f"{'IS PF':>6} | Overfit"
    )
    print(header)
    print(sep)
    for i, s in enumerate(summaries, 1):
        overfit_mark = "[!]" if s["overfit_flag"] else "   "
        print(
            f"{i:>2} | {int(s['fast_ma']):>4} | {int(s['slow_ma']):>4} | "
            f"{int(s['rsi_oversold']):>3} | {int(s['rsi_overbought']):>3} | "
            f"{int(s['stop_loss_pct']):>4} | {int(s['target_pct']):>5} | "
            f"{s['total_folds']:>5} | "
            f"{s['profitable_folds']:>6} | "
            f"{s['consistency_pct']:>8.1f} | "
            f"{s['mean_oos_pf']:>6.3f} | "
            f"{s['std_oos_pf']:>7.3f} | "
            f"{s['mean_is_pf']:>6.3f} | {overfit_mark}"
        )
    print(sep)


# ─────────────────────────────────────────────────────────────────
# Print final recommendation
# ─────────────────────────────────────────────────────────────────

def _print_recommendation(summaries: list[dict]) -> None:
    """
    Find the best parameter set by:
      1. Highest consistency score (% of profitable folds)
      2. Tiebreak: highest mean OOS profit factor
      3. Exclude overfit-flagged sets unless all are flagged
    """
    if not summaries:
        return

    # Prefer non-overfit sets
    candidates = [s for s in summaries if not s["overfit_flag"]]
    if not candidates:
        candidates = summaries  # all overfit, use best available

    best = max(
        candidates,
        key=lambda s: (s["consistency_pct"], s["mean_oos_pf"]),
    )
    min_trades = getattr(config, "MIN_REGIME_TRADES_FOR_RECOMMENDATION", 5)

    n_folds = best["total_folds"]
    n_profit = best["profitable_folds"]
    oos_pf   = best["mean_oos_pf"]
    overfit_note = " [CAUTION: overfit flag]" if best["overfit_flag"] else ""

    print(f"\n{'='*70}")
    print("  RECOMMENDATION")
    print(f"{'='*70}")
    if best.get("total_test_trades", 0) < min_trades:
        print(
            "  No automatic parameter recommendation: "
            f"best set has {best.get('total_test_trades', 0)} OOS trades, "
            f"below minimum sample {min_trades}."
        )
        print("  Inspect MA/RSI and SL/target sensitivity by regime before changing config.")
        print(f"{'='*70}\n")
        return
    print(
        f"  RECOMMENDED PARAMS: "
        f"fast_ma={int(best['fast_ma'])}, slow_ma={int(best['slow_ma'])}, "
        f"rsi_oversold={int(best['rsi_oversold'])}, rsi_overbought={int(best['rsi_overbought'])}, "
        f"stop_loss_pct={int(best['stop_loss_pct'])}, target_pct={int(best['target_pct'])}"
    )
    print(
        f"  Consistent across {n_profit}/{n_folds} folds "
        f"({best['consistency_pct']:.1f}% profitable), "
        f"avg OOS profit_factor={oos_pf:.3f}{overfit_note}"
    )
    print(f"{'='*70}\n")


def _print_regime_summary(results_df: pd.DataFrame) -> None:
    if results_df.empty or "regime" not in results_df:
        return
    grouped = (
        results_df
        .groupby("regime")
        .agg(
            folds=("fold", "count"),
            trades=("test_trades", "sum"),
            avg_pf=("test_pf", "mean"),
            avg_return=("test_return_pct", "mean"),
        )
        .reset_index()
    )
    print(f"\n{'='*70}")
    print("  REGIME SUMMARY")
    print(f"{'='*70}")
    for row in grouped.to_dict(orient="records"):
        print(
            f"  {row['regime']}: folds={int(row['folds'])}, "
            f"trades={int(row['trades'])}, avg_pf={row['avg_pf']:.3f}, "
            f"avg_return={row['avg_return']:+.2f}%"
        )
    print(f"{'='*70}\n")


# ─────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Example: run walk-forward on the default baseline params
    baseline = [{
        "fast_ma"       : config.TREND_FAST_MA,
        "slow_ma"       : config.TREND_SLOW_MA,
        "rsi_oversold"  : config.RSI_OVERSOLD,
        "rsi_overbought": config.RSI_OVERBOUGHT,
        "stop_loss_pct" : config.BUY_STOP_LOSS_PCT,
        "target_pct"    : config.BUY_TARGET_PCT,
    }]
    run_walk_forward(baseline)
