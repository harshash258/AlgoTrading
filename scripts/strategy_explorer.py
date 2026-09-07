"""
strategy_explorer.py — Strategy Combination Tester.

Tests every single strategy, every pair, and every multi-strategy
combination (up to a configurable max size) using CombinedStrategy.
Ranks results by profit factor, Sharpe, CAGR, and consistency.

Usage
-----
  # Test all singles and pairs (default)
  python strategy_explorer.py

  # Test up to 3-strategy combinations
  python strategy_explorer.py --max-combo 3

  # Run only specific strategies
  python strategy_explorer.py --include trend rsi straddle iron_condor

  # Exclude strategies from the sweep
  python strategy_explorer.py --exclude inv_trend inv_rsi

  # Sort results by a different metric
  python strategy_explorer.py --sort cagr_pct

  # Skip loading bhavcopy (faster, uses Black-Scholes)
  python strategy_explorer.py --no-bhavcopy

  # Run on a single ticker only
  python strategy_explorer.py --ticker ^NSEI

  # Minimum trades filter
  python strategy_explorer.py --min-trades 20

Output
------
  reports/strategy_explorer_YYYY-MM-DD.csv   — full ranked results
  Console table printed after completion
"""

import argparse
import logging
import os
import sys
from datetime import date
from itertools import combinations

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import config
from algo_trading.data.ingestion import get_combined_dataset, load_bhavcopy_folder, ChainLookup
from algo_trading.core.backtester import Backtester
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.strategies import (
    CombinedStrategy,
    TrendFollowingStrategy,
    RSIStrategy,
    MeanReversionStrategy,
    BollingerBandStrategy,
    ConfluenceStrategy,
    InverseStrategy,
    ORBStrategy,
    LongStraddleStrategy,
    VWAPReversionStrategy,
    GapFadeStrategy,
    IronCondorStrategy,
)
import algo_trading.core.backtester as _backtester_module

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Strategy registry — all available strategies
# Keys are short names used in --include / --exclude flags.
# Each value is a factory function (called at test time so state
# is fresh for every combination run).
# ─────────────────────────────────────────────────────────────────

def _build_registry() -> dict:
    """Return a dict of name → factory_fn for every strategy."""
    from algo_trading.strategies.vertical_spread import BullCallSpread, BearPutSpread, BullPutSpread, BearCallSpread
    return {
        "bull_call_spread": BullCallSpread,
        "bear_put_spread": BearPutSpread,
        "bull_put_spread": BullPutSpread,
        "bear_call_spread": BearCallSpread,
        # ── Directional / trend ───────────────────────────────────
        "trend": lambda: TrendFollowingStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            use_adx_filter=False,
            use_confirm=False,
            use_direction_filter=False,
            use_time_stop=False,
        ),
        "trend_full": lambda: TrendFollowingStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            use_adx_filter=True,
            use_confirm=True,
            use_direction_filter=True,
            use_time_stop=True,
        ),
        "rsi": lambda: RSIStrategy(
            rsi_period=config.RSI_PERIOD,
            oversold=config.RSI_OVERSOLD,
            overbought=config.RSI_OVERBOUGHT,
            confirm_bars=config.RSI_CONFIRM_BARS,
            time_stop_days=config.RSI_TIME_STOP_DAYS,
            weekly=True,
        ),
        "confluence": lambda: ConfluenceStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            rsi_period=config.RSI_PERIOD,
            rsi_oversold=config.RSI_OVERSOLD,
            rsi_overbought=config.RSI_OVERBOUGHT,
            rsi_lookback=config.CONFLUENCE_RSI_LOOKBACK,
            rsi_entry_max=config.CONFLUENCE_RSI_ENTRY_MAX,
            rsi_entry_min=config.CONFLUENCE_RSI_ENTRY_MIN,
            use_trend_filter=config.CONFLUENCE_USE_TREND_FILTER,
            vix_min=config.BB_VIX_MIN,
            vix_max=config.BB_VIX_MAX,
            time_stop_days=config.CONFLUENCE_TIME_STOP_DAYS,
            weekly=True,
        ),
        "orb": lambda: ORBStrategy(
            breakout_pct=config.ORB_BREAKOUT_PCT,
            gap_max_pct=config.ORB_GAP_MAX_PCT,
            use_adx_filter=config.ORB_USE_ADX_FILTER,
            time_stop_days=config.ORB_TIME_STOP_DAYS,
            weekly=True,
        ),
        # ── Mean reversion / short vol ────────────────────────────
        "mean_rev": lambda: MeanReversionStrategy(
            iv_entry_pct=config.MR_IV_PERCENTILE_ENTRY,
            iv_exit_pct=config.MR_IV_PERCENTILE_EXIT,
            otm_delta=config.MR_OTM_DELTA,
            weekly=False,
        ),
        "bb": lambda: BollingerBandStrategy(
            bb_period=config.BB_PERIOD,
            bb_std=config.BB_STD_MULT,
            atr_period=config.BB_ATR_PERIOD,
            use_trend_filter=config.BB_USE_TREND_FILTER,
            vix_min=config.BB_VIX_MIN,
            vix_max=config.BB_VIX_MAX,
            confirm_bars=config.BB_CONFIRM_BARS,
            time_stop_days=config.BB_TIME_STOP_DAYS,
            weekly=True,
        ),
        "iron_condor": lambda: IronCondorStrategy(
            iv_entry_pct=config.IC_IV_ENTRY_PCT,
            iv_exit_pct=config.IC_IV_EXIT_PCT,
            body_delta=config.IC_BODY_DELTA,
            wing_delta=config.IC_WING_DELTA,
            use_adx_filter=config.IC_USE_ADX_FILTER,
            adx_choppy_threshold=config.IC_ADX_CHOPPY_THRESHOLD,
            time_stop_days=config.IC_TIME_STOP_DAYS,
            weekly=False,
        ),
        # ── Long volatility ───────────────────────────────────────
        "straddle": lambda: LongStraddleStrategy(
            iv_entry_pct=config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct=config.STRADDLE_IV_EXIT_PCT,
            otm_delta=0.0,
            time_stop_days=config.STRADDLE_TIME_STOP_DAYS,
            weekly=True,
        ),
        "strangle": lambda: LongStraddleStrategy(
            iv_entry_pct=config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct=config.STRADDLE_IV_EXIT_PCT,
            otm_delta=0.25,
            time_stop_days=config.STRADDLE_TIME_STOP_DAYS,
            weekly=True,
        ),
        # ── Intraday-proxy ────────────────────────────────────────
        "vwap_rev": lambda: VWAPReversionStrategy(
            vwap_window=config.VWAP_WINDOW,
            std_mult=config.VWAP_STD_MULT,
            mode="reversion",
            time_stop_days=config.VWAP_TIME_STOP_DAYS,
            weekly=True,
        ),
        "vwap_brk": lambda: VWAPReversionStrategy(
            vwap_window=config.VWAP_WINDOW,
            std_mult=config.VWAP_STD_MULT,
            mode="breakout",
            time_stop_days=config.VWAP_TIME_STOP_DAYS,
            weekly=True,
        ),
        "gap_fade": lambda: GapFadeStrategy(
            gap_min_pct=config.GAP_MIN_PCT,
            gap_max_pct=config.GAP_MAX_PCT,
            require_no_follow=config.GAP_REQUIRE_NO_FOLLOW,
            trend_filter=config.GAP_TREND_FILTER,
            time_stop_days=config.GAP_TIME_STOP_DAYS,
            weekly=True,
        ),
    }


# ─────────────────────────────────────────────────────────────────
# Build all combinations up to max_combo size
# ─────────────────────────────────────────────────────────────────

def _build_combos(
    registry: dict,
    max_combo: int,
    include: list[str] | None,
    exclude: list[str] | None,
) -> list[tuple[str, ...]]:
    """
    Return a list of strategy name-tuples to test.
    Sizes 1 through max_combo, using combinations (order doesn't matter —
    CombinedStrategy merges signals so A+B == B+A).
    """
    names = list(registry.keys())

    if include:
        missing = [n for n in include if n not in registry]
        if missing:
            print(f"ERROR: Unknown strategies in --include: {missing}")
            print(f"Available: {names}")
            sys.exit(1)
        names = [n for n in names if n in include]

    if exclude:
        names = [n for n in names if n not in exclude]

    combos: list[tuple[str, ...]] = []
    for size in range(1, min(max_combo, len(names)) + 1):
        for combo in combinations(names, size):
            combos.append(combo)

    return combos


# ─────────────────────────────────────────────────────────────────
# Run one combo backtest
# ─────────────────────────────────────────────────────────────────

def _run_combo(
    combo: tuple[str, ...],
    registry: dict,
    market_data: dict,
    chain_lookup,
    start: date,
    end: date,
) -> dict | None:
    """
    Instantiate fresh strategy objects, run the backtest, compute metrics.
    Returns a flat result dict or None on failure.
    """
    try:
        strategies = [registry[name]() for name in combo]

        if len(strategies) == 1:
            strategy = strategies[0]
        else:
            strategy = CombinedStrategy(strategies)


        bt = Backtester(
            strategy=strategy,
            data=market_data,
            start=start,
            end=end,
            starting_capital=config.STARTING_CAPITAL,
            chain_lookups=chain_lookup,
        )
        trades = bt.run()
        equity_curve = bt.get_equity_curve()

        if not trades:
            return None

        metrics = compute_metrics(trades, equity_curve, config.STARTING_CAPITAL)

        return {
            "combo"            : " + ".join(combo),
            "combo_size"       : len(combo),
            "strategies"       : list(combo),
            "total_trades"     : metrics["total_trades"],
            "win_rate_pct"     : metrics["win_rate_pct"],
            "total_return_pct" : metrics["total_return_pct"],
            "cagr_pct"         : metrics["cagr_pct"],
            "profit_factor"    : metrics["profit_factor"],
            "sharpe_ratio" : metrics["sharpe_ratio"],
            "sortino_ratio": metrics["sortino_ratio"],
            "max_drawdown_pct" : metrics["max_drawdown_pct"],
            "avg_held_days"    : metrics["avg_held_days"],
            "net_pnl"          : metrics["net_pnl"],
            "final_capital"    : metrics["final_capital"],
        }

    except Exception as e:
        logger.debug(f"Combo {combo} failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# Progress display
# ─────────────────────────────────────────────────────────────────

def _progress(current: int, total: int, best: dict | None) -> None:
    if best:
        best_str = f"PF={best['profit_factor']:.3f} [{best['combo']}]"
    else:
        best_str = "none yet"
    line = f"  Testing {current:>4}/{total} | Best so far: {best_str}"
    print(f"\r{line:<110}", end="", flush=True)


# ─────────────────────────────────────────────────────────────────
# Results table printer
# ─────────────────────────────────────────────────────────────────

def _print_table(df: pd.DataFrame, title: str, n: int = 20) -> None:
    sep = "─" * 120
    print(f"\n{'═'*120}")
    print(f"  {title}")
    print(f"{'═'*120}")
    header = (
        f"{'#':>4} | {'Size':>4} | {'Trades':>6} | {'WR%':>6} | "
        f"{'Ret%':>7} | {'CAGR%':>6} | {'PF':>6} | {'Sharpe':>7} | "
        f"{'DD%':>6} | {'AvgHeld':>7} | Combination"
    )
    print(header)
    print(sep)
    for i, (_, row) in enumerate(df.head(n).iterrows(), 1):
        pf_str = f"{row['profit_factor']:.3f}" if row["profit_factor"] != float("inf") else "Inf"
        combo_str = row["combo"]
        # Truncate long combo names for display
        if len(combo_str) > 45:
            combo_str = combo_str[:42] + "..."
        print(
            f"{i:>4} | {int(row['combo_size']):>4} | {int(row['total_trades']):>6} | "
            f"{row['win_rate_pct']:>6.2f} | {row['total_return_pct']:>+7.2f} | "
            f"{row['cagr_pct']:>+6.2f} | {pf_str:>6} | "
            f"{row['sharpe_ratio']:>7.3f} | {row['max_drawdown_pct']:>6.2f} | "
            f"{row['avg_held_days']:>7.1f} | {combo_str}"
        )
    print(sep)


# ─────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────

def run_explorer(
    max_combo    : int       = 2,
    include      : list[str] | None = None,
    exclude      : list[str] | None = None,
    sort_by      : str       = "profit_factor",
    tickers      : list[str] | None = None,
    min_trades   : int       = 10,
    use_bhavcopy : bool      = True,
    start        : date | None = None,
    end          : date | None = None,
) -> pd.DataFrame:
    """
    Run all strategy combinations and rank by performance.

    Parameters
    ----------
    max_combo    : Maximum combo size (1=singles, 2=pairs+singles, 3=triples...)
    include      : Only test these strategy names (None = all)
    exclude      : Skip these strategy names
    sort_by      : Metric to sort results by (default: profit_factor)
    tickers      : Underlyings (default: config.UNDERLYINGS)
    min_trades   : Skip results with fewer trades than this
    use_bhavcopy : Load bhavcopy chain for real pricing (slower but accurate)
    start / end  : Backtest period (default: config dates)

    Returns
    -------
    DataFrame of all results, sorted by sort_by descending.
    """
    start   = start   or config.BACKTEST_START
    end     = end     or config.BACKTEST_END
    tickers = tickers or config.UNDERLYINGS

    registry = _build_registry()
    combos   = _build_combos(registry, max_combo, include, exclude)

    total = len(combos)

    print(f"\n{'='*70}")
    print(f"  STRATEGY EXPLORER")
    print(f"{'='*70}")
    print(f"  Available strategies : {len(registry)}")
    print(f"  Max combo size       : {max_combo}")
    print(f"  Combinations to test : {total}")
    print(f"  Tickers              : {tickers}")
    print(f"  Period               : {start} → {end}")
    print(f"  Min trades filter    : {min_trades}")
    print(f"  Sort by              : {sort_by}")
    print(f"{'='*70}")

    # ── Load market data ──────────────────────────────────────────
    print("\nLoading market data...")
    market_data = get_combined_dataset(start=start, end=end)
    market_data = {k: v for k, v in market_data.items() if k in tickers}
    if not market_data:
        raise RuntimeError(f"No market data for: {tickers}")
    print(f"  Loaded: {list(market_data.keys())}")

    # The engine lazily loads each underlying's own chain. An empty mapping
    # explicitly disables market data for synthetic-only research.
    chain_lookup = None if use_bhavcopy else {}
    _blanked_bhavcopy = False

    # ── Run all combinations ──────────────────────────────────────
    print(f"\nRunning {total} backtest(s)...\n")

    all_results  : list[dict] = []
    best_so_far  : dict | None = None
    failed       : int = 0

    try:
        for i, combo in enumerate(combos, 1):
            _progress(i, total, best_so_far)

            result = _run_combo(combo, registry, market_data, chain_lookup, start, end)

            if result is None or result["total_trades"] < min_trades:
                failed += 1
                continue

            all_results.append(result)

            pf = result["profit_factor"]
            if (
                best_so_far is None
                or pf > best_so_far["profit_factor"]
                or (pf == best_so_far["profit_factor"]
                    and result["sharpe_ratio"] > best_so_far["sharpe_ratio"])
            ):
                best_so_far = result

    finally:
        # Always restore BHAVCOPY_FOLDER if we blanked it
        if _blanked_bhavcopy:
            config.BHAVCOPY_FOLDER = _orig_bhavcopy_folder

    print()  # newline after progress bar

    if not all_results:
        print(f"\nNo results passed the minimum trades filter ({min_trades}).")
        return pd.DataFrame()

    # ── Sort and rank ─────────────────────────────────────────────
    results_df = pd.DataFrame(all_results)

    valid_cols = [c for c in results_df.columns if c != "strategies"]
    sort_col   = sort_by if sort_by in results_df.columns else "profit_factor"
    results_df = results_df.sort_values(by=sort_col, ascending=False).reset_index(drop=True)
    results_df.insert(0, "rank", results_df.index + 1)

    # ── Save CSV ──────────────────────────────────────────────────
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    csv_path = os.path.join(
        config.REPORTS_DIR,
        f"strategy_explorer_{date.today().isoformat()}.csv"
    )
    save_cols = [c for c in results_df.columns if c != "strategies"]
    results_df[save_cols].to_csv(csv_path, index=False, encoding="utf-8")

    # ── Print summary stats ───────────────────────────────────────
    print(f"\n  Total combinations run : {total}")
    print(f"  Passed min-trades filter: {len(all_results)}")
    print(f"  Skipped / no trades     : {failed}")

    # ── Print ranked tables ───────────────────────────────────────
    _print_table(results_df, f"TOP COMBINATIONS — sorted by {sort_col}", n=20)

    # ── Breakdown by combo size ───────────────────────────────────
    for size in sorted(results_df["combo_size"].unique()):
        subset = results_df[results_df["combo_size"] == size]
        label  = {1: "SINGLE STRATEGIES", 2: "PAIRS", 3: "TRIPLES"}.get(
            size, f"{size}-STRATEGY COMBOS"
        )
        _print_table(subset.reset_index(drop=True), f"TOP {label}", n=10)

    # ── Best single strategy callout ─────────────────────────────
    singles = results_df[results_df["combo_size"] == 1]
    if not singles.empty:
        best_single = singles.iloc[0]
        print(f"\n  Best single strategy  : {best_single['combo']}")
        print(f"    PF={best_single['profit_factor']:.3f}  "
              f"CAGR={best_single['cagr_pct']:+.2f}%  "
              f"WR={best_single['win_rate_pct']:.1f}%  "
              f"DD={best_single['max_drawdown_pct']:.2f}%")

    # ── Best overall callout ──────────────────────────────────────
    best_overall = results_df.iloc[0]
    print(f"\n  Best overall combination: {best_overall['combo']}")
    print(f"    PF={best_overall['profit_factor']:.3f}  "
          f"CAGR={best_overall['cagr_pct']:+.2f}%  "
          f"WR={best_overall['win_rate_pct']:.1f}%  "
          f"Sharpe={best_overall['sharpe_ratio']:.3f}  "
          f"DD={best_overall['max_drawdown_pct']:.2f}%")

    # ── Vol balance check ─────────────────────────────────────────
    _print_vol_balance_note(results_df)

    print(f"\n  Full results saved to: {csv_path}\n")

    return results_df


# ─────────────────────────────────────────────────────────────────
# Portfolio balance note — short vol vs long vol
# ─────────────────────────────────────────────────────────────────

def _print_vol_balance_note(df: pd.DataFrame) -> None:
    """
    Among top-10 combos, highlight whether long-vol and short-vol strategies
    are paired (the natural hedge that motivated adding LongStraddle).
    """
    SHORT_VOL = {"mean_rev", "iron_condor", "bb"}
    LONG_VOL  = {"straddle", "strangle"}

    balanced = []
    for _, row in df.head(10).iterrows():
        strats = set(row["combo"].split(" + "))
        has_short = bool(strats & SHORT_VOL)
        has_long  = bool(strats & LONG_VOL)
        if has_short and has_long:
            balanced.append(row["combo"])

    if balanced:
        print(f"\n  Vol-balanced combos in top 10 (short + long vol paired):")
        for c in balanced:
            print(f"    ✓ {c}")
    else:
        print(f"\n  No vol-balanced combos (short+long vol pair) in top 10.")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Strategy Explorer — test all combinations and find the best",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--max-combo", type=int, default=2, metavar="N",
        help="Maximum number of strategies per combination (default: 2). "
             "Warning: 3 generates ~500+ combos, 4+ gets very slow.",
    )
    parser.add_argument(
        "--include", nargs="+", metavar="STRATEGY",
        help="Only test these strategies (space-separated). "
             "Example: --include trend rsi straddle iron_condor",
    )
    parser.add_argument(
        "--exclude", nargs="+", metavar="STRATEGY",
        help="Skip these strategies from the sweep.",
    )
    parser.add_argument(
        "--sort", default="profit_factor", metavar="METRIC",
        choices=[
            "profit_factor", "cagr_pct", "total_return_pct",
            "sharpe_ratio", "sortino_ratio", "win_rate_pct",
            "max_drawdown_pct", "total_trades",
        ],
        help="Metric to sort results by (default: profit_factor)",
    )
    parser.add_argument(
        "--ticker", action="append", metavar="TICKER",
        help="Ticker(s) to trade (default: all in config). Repeatable.",
    )
    parser.add_argument(
        "--min-trades", type=int, default=10, metavar="N",
        help="Minimum trades to include a result (default: 10)",
    )
    parser.add_argument(
        "--no-bhavcopy", action="store_true",
        help="Skip bhavcopy loading — faster but uses Black-Scholes pricing",
    )
    parser.add_argument(
        "--start", metavar="YYYY-MM-DD",
        help="Backtest start date (default: config.BACKTEST_START)",
    )
    parser.add_argument(
        "--end", metavar="YYYY-MM-DD",
        help="Backtest end date (default: config.BACKTEST_END)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all available strategy names and exit",
    )

    args = parser.parse_args()

    # --list: print available strategies and exit
    if args.list:
        registry = _build_registry()
        print("\nAvailable strategies:")
        for name in registry:
            print(f"  {name}")
        print(f"\nTotal: {len(registry)}")
        return

    run_explorer(
        max_combo    = args.max_combo,
        include      = args.include,
        exclude      = args.exclude,
        sort_by      = args.sort,
        tickers      = args.ticker or config.UNDERLYINGS,
        min_trades   = args.min_trades,
        use_bhavcopy = not args.no_bhavcopy,
        start        = date.fromisoformat(args.start) if args.start else None,
        end          = date.fromisoformat(args.end)   if args.end   else None,
    )


if __name__ == "__main__":
    main()
