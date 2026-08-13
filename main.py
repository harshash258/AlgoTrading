"""
main.py — Entry point for the Algo Trading system.

Usage
-----
  # Run a backtest
  python main.py backtest --strategy trend --ticker ^NSEI

  # Run a backtest with mean reversion strategy
  python main.py backtest --strategy mean_rev --ticker ^NSEI

  # Run Bollinger Band mean reversion strategy
  python main.py backtest --strategy bb --ticker ^NSEI

  # Run confluence strategy (MA crossover + RSI agreement)
  python main.py backtest --strategy confluence --ticker ^NSEI

  # Opening Range Breakout (daily proxy)
  python main.py backtest --strategy orb --ticker ^NSEI

  # Long Straddle — buy vol on low-IV days
  python main.py backtest --strategy straddle --ticker ^NSEI

  # Long Strangle — OTM version of straddle
  python main.py backtest --strategy strangle --ticker ^NSEI

  # VWAP Reversion — fade VWAP band extensions
  python main.py backtest --strategy vwap_rev --ticker ^NSEI

  # VWAP Breakout — follow VWAP band breaks
  python main.py backtest --strategy vwap_brk --ticker ^NSEI

  # Gap Fade — fade opening gaps with no follow-through
  python main.py backtest --strategy gap_fade --ticker ^NSEI

  # Iron Condor — delta-neutral income on choppy days
  python main.py backtest --strategy iron_condor --ticker ^NSEI

  # Generate today's signals (after backtest has run)
  python main.py signals --strategy combined --ticker ^NSEI

  # Refresh data cache
  python main.py fetch --ticker ^NSEI --ticker ^NSEBANK

  # Run Level 1 grid search optimization
  python main.py optimize

  # Run Level 1 then Level 2 walk-forward on top 5 param sets
  python main.py optimize --top 5

  # Quick optimization with reduced grid (~144 combos)
  python main.py optimize --quick
"""

import argparse
import logging
import os
import re
import sys
from datetime import date

# ── Ensure workspace / src in path ───────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config
from algo_trading.core.backtester import Backtester
from algo_trading.data.ingestion import get_combined_dataset, fetch_all_underlyings
from algo_trading.data.bhavcopy import download_range, download_last_n_days, verify_downloads
from algo_trading.reporting.metrics import compute_metrics, trades_to_dataframe, print_summary
from algo_trading.reporting.html_generator import generate_html_report
from algo_trading.strategies import (
    get_strategy as registry_get_strategy,
    list_strategies,
    InverseStrategy,
    CombinedStrategy,
    TrendFollowingStrategy,
    RSIStrategy,
)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Strategy factory ─────────────────────────────────────────────
def get_strategy(name: str):
    """
    Look up strategy from the central registry, with support for
    inversion prefixes (`inv_<name>`) and legacy custom combined instances.
    """
    name_clean = name.strip().lower()
    
    # Check for inverse wrapper
    if name_clean.startswith("inv_"):
        base_name = name_clean[4:]
        base_strat = get_strategy(base_name)
        return InverseStrategy(base_strat)
    
    # Custom combined with default components
    if name_clean == "combined":
        return CombinedStrategy([
            TrendFollowingStrategy(
                fast_ma              = config.TREND_FAST_MA,
                slow_ma              = config.TREND_SLOW_MA,
                use_adx_filter       = False,
                use_confirm          = False,
                use_direction_filter = False,
                use_time_stop        = False,
            ),
            RSIStrategy(
                rsi_period     = config.RSI_PERIOD,
                oversold       = config.RSI_OVERSOLD,
                overbought     = config.RSI_OVERBOUGHT,
                confirm_bars   = config.RSI_CONFIRM_BARS,
                time_stop_days = config.RSI_TIME_STOP_DAYS,
                weekly         = True,
            ),
        ])
    
    try:
        return registry_get_strategy(name_clean)
    except ValueError:
        print(f"Unknown strategy '{name}'. Available: {list_strategies()}")
        sys.exit(1)


# ── Commands ─────────────────────────────────────────────────────

def cmd_backtest(args):
    """Run a full backtest and generate HTML report."""
    tickers = args.ticker or config.UNDERLYINGS
    logger.info(f"Running backtest: {args.strategy} on {tickers}")

    # 1. Fetch data
    logger.info("Loading market data...")
    data = get_combined_dataset(
        start=config.BACKTEST_START,
        end=config.BACKTEST_END,
        force_refresh=args.refresh,
    )
    # Filter to requested tickers
    data = {k: v for k, v in data.items() if k in tickers}

    if not data:
        logger.error(f"No data found for tickers: {tickers}")
        sys.exit(1)

    # 2. Run backtest
    strategy = get_strategy(args.strategy)
    bt = Backtester(
        strategy=strategy,
        data=data,
        start=config.BACKTEST_START,
        end=config.BACKTEST_END,
        starting_capital=config.STARTING_CAPITAL,
    )
    trades = bt.run()
    equity_curve = bt.get_equity_curve()

    # 3. Print console summary
    metrics = compute_metrics(trades, equity_curve, config.STARTING_CAPITAL)
    print_summary(metrics, strategy.name)

    # 4. Save trade log CSV
    if trades:
        trades_df = trades_to_dataframe(trades)
        csv_path  = os.path.join(
            config.REPORTS_DIR,
            f"trades_{strategy.name}_{date.today().isoformat()}.csv"
        )
        os.makedirs(config.REPORTS_DIR, exist_ok=True)
        trades_df.to_csv(csv_path, index=False)
        logger.info(f"Trade log saved: {csv_path}")

    # 5. Generate HTML report
    html_path = generate_html_report(
        trades=trades,
        equity_curve=equity_curve,
        strategy_name=strategy.name,
        strategy_params=strategy.get_params(),
        underlyings=tickers,
        backtest_start=config.BACKTEST_START,
        backtest_end=config.BACKTEST_END,
    )
    print(f"\n✅ HTML report: {html_path}")
    print("   Open this file in your browser to view the dashboard.\n")


def cmd_signals(args):
    """Generate today's trading signals and print to console."""
    from algo_trading.notifications.telegram import generate_signal_message

    strategy = get_strategy(args.strategy) if args.strategy != "combined" else None

    print("\nGenerating signals...\n")
    msg = generate_signal_message(strategy=strategy)

    # Strip HTML tags for clean console output
    print(re.sub(r"<[^>]+>", "", msg))


def cmd_fetch(args):
    """Download / refresh market data cache."""
    tickers = args.ticker or config.UNDERLYINGS
    logger.info(f"Fetching data for: {tickers}")
    data = get_combined_dataset(force_refresh=True)
    for ticker, df in data.items():
        if ticker in tickers:
            print(f"  {ticker}: {len(df)} rows | {df.index.min().date()} → {df.index.max().date()}")
    print("\nData cache updated.\n")


def cmd_optimize(args):
    """Run Level 1 grid search optimization, optionally followed by Level 2 walk-forward."""
    # Suppress per-trade INFO logs during optimization runs for clean progress output
    logging.getLogger("algo_trading.core.backtester").setLevel(logging.WARNING)
    logging.getLogger("algo_trading.data.ingestion").setLevel(logging.WARNING)
    logging.getLogger("algo_trading.core.pricing").setLevel(logging.WARNING)
    logging.getLogger("algo_trading.core.risk_manager").setLevel(logging.WARNING)

    from algo_trading.optimization.grid_search import run_optimization
    from algo_trading.optimization.walk_forward import run_walk_forward

    tickers = args.ticker or config.UNDERLYINGS

    # ── Level 1: Grid search ──────────────────────────────────────
    results_df, best_params = run_optimization(
        quick=args.quick,
        tickers=tickers,
        min_trades=args.min_trades,
    )

    if results_df.empty:
        print("\nOptimization produced no valid results. Exiting.")
        return

    # ── Level 2: Walk-forward (if --top N specified) ──────────────
    if args.top and args.top > 0:
        n = min(args.top, len(results_df))
        print(f"\n\nRunning walk-forward validation on top {n} parameter sets...")

        top_params = results_df.head(n).to_dict(orient="records")
        # Remove 'rank' key before passing to walk_forward
        for p in top_params:
            p.pop("rank", None)

        run_walk_forward(top_params, tickers=tickers)


def cmd_download_bhavcopy(args):
    """Download NSE F&O bhavcopy files for real options chain data."""
    from datetime import timedelta
    output_dir = os.path.join("data", "bhavcopy")

    if args.days:
        logger.info(f"Downloading last {args.days} days of bhavcopy...")
        download_last_n_days(args.days, output_dir)
    else:
        start = date.fromisoformat(args.start) if args.start else config.BACKTEST_START
        end   = date.fromisoformat(args.end)   if args.end   else date.today() - timedelta(days=1)
        logger.info(f"Downloading bhavcopy: {start} → {end}")
        download_range(start, end, output_dir, overwrite=args.overwrite)

    if args.verify:
        verify_downloads(output_dir)


def cmd_screen_stocks(args):
    """Screen stocks against multiple predefined strategies."""
    from algo_trading.screener.stock_screener import StockScreener, format_screening_results

    strategy_names = args.strategy or ["cheap_to_moon"]
    tickers = args.ticker
    universe_file = args.universe
    max_workers = getattr(args, 'max_workers', 4)  # Default to 4 if not provided

    screener = StockScreener()
    screener.add_strategies(strategy_names)

    logger.info(f"Screening against {len(strategy_names)} strategies: {strategy_names}")

    results = screener.search(
        tickers=tickers,
        universe_file=universe_file,
        max_workers=max_workers,
    )

    if not results:
        print("No results.")
        return

    # Print results
    report = format_screening_results(results)
    print("\n" + report + "\n")

    # Save to CSV if any matches found
    csv_path = os.path.join(
        config.REPORTS_DIR,
        f"screen_{'_'.join(strategy_names)}_{date.today().isoformat()}.csv"
    )
    os.makedirs(config.REPORTS_DIR, exist_ok=True)

    # Flatten results to CSV
    rows = []
    for strategy_name, matches in results.items():
        for match in matches:
            if match["passes"]:
                m = match["metrics"]
                rows.append({
                    "strategy": strategy_name,
                    "ticker": match["ticker"],
                    "price": m.get("price"),
                    "market_cap_cr": m.get("market_cap_cr"),
                    "pe": m.get("pe"),
                    "pb": m.get("pb"),
                    "roce": m.get("roce"),
                    "roe": m.get("roe"),
                    "debt_to_equity": m.get("debt_to_equity"),
                    "up_52w_pct": m.get("up_52w_pct"),
                })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)
        logger.info(f"Results saved: {csv_path}")
        print(f"📊 CSV saved: {csv_path}\n")


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Algo Trading System — NSE Options",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest
    bt_parser = sub.add_parser("backtest", help="Run backtest and generate report")
    bt_parser.add_argument("--strategy", default="combined",
                           help="Strategy: trend | rsi | combined | mean_rev | bb | confluence"
                                " | orb | straddle | strangle | vwap_rev | vwap_brk"
                                " | gap_fade | iron_condor"
                                " | inv_<any> to invert (default: combined)")
    bt_parser.add_argument("--ticker", action="append",
                           help="Ticker(s) to trade (default: all in config)")
    bt_parser.add_argument("--refresh", action="store_true",
                           help="Force re-download of market data")

    # signals
    sig_parser = sub.add_parser("signals", help="Generate today's trading signals")
    sig_parser.add_argument("--strategy", default="combined")
    sig_parser.add_argument("--ticker", action="append")

    # fetch
    fetch_parser = sub.add_parser("fetch", help="Download/refresh market data cache")
    fetch_parser.add_argument("--ticker", action="append")

    # bhavcopy
    bh_parser = sub.add_parser("bhavcopy", help="Download NSE F&O bhavcopy option chain data")
    bh_parser.add_argument("--start",    help="Start date YYYY-MM-DD (default: backtest start)")
    bh_parser.add_argument("--end",      help="End date YYYY-MM-DD (default: yesterday)")
    bh_parser.add_argument("--days",     type=int, help="Download last N calendar days (quick test)")
    bh_parser.add_argument("--overwrite",action="store_true", help="Re-download existing files")
    bh_parser.add_argument("--verify",   action="store_true", help="Verify ZIP files after download")

    # optimize
    opt_parser = sub.add_parser(
        "optimize",
        help="Run grid search optimization (Level 1), optionally with walk-forward (Level 2)",
    )
    opt_parser.add_argument(
        "--quick", action="store_true",
        help="Use reduced grid (~144 combos) for a faster run",
    )
    opt_parser.add_argument(
        "--top", type=int, default=0, metavar="N",
        help="After Level 1, run Level 2 walk-forward on the top N param sets (e.g. --top 5)",
    )
    opt_parser.add_argument(
        "--min-trades", type=int, default=10, dest="min_trades", metavar="N",
        help="Minimum trades required to include a result (default: 10)",
    )
    opt_parser.add_argument(
        "--ticker", action="append",
        help="Ticker(s) to optimise on (default: all in config)",
    )

    # screen
    screen_parser = sub.add_parser("screen", help="Search for stocks matching screening criteria")
    screen_parser.add_argument(
        "--strategy", action="append",
        help="Strategy to screen: cheap_to_moon | multibagger (default: cheap_to_moon)",
    )
    screen_parser.add_argument(
        "--ticker", action="append",
        help="Ticker(s) to screen (e.g. RELIANCE.NS TCS.NS)",
    )
    screen_parser.add_argument(
        "--universe",
        help="Path to file with newline-separated ticker list (e.g. nse_tickers.txt)",
    )
    screen_parser.add_argument(
        "--max-workers", type=int, default=4, dest="max_workers", metavar="N",
        help="Number of parallel workers for fetching stock data (default: 4, max recommended: 8)",
    )

    args = parser.parse_args()
    if args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "signals":
        cmd_signals(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "bhavcopy":
        cmd_download_bhavcopy(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "screen":
        cmd_screen_stocks(args)


if __name__ == "__main__":
    main()
