"""
main.py — Entry point for the Algo Trading system.

Usage
-----
  # Run a backtest
  python main.py backtest --strategy trend --ticker ^NSEI

  # Run a backtest with mean reversion strategy
  python main.py backtest --strategy mean_rev --ticker ^NSEI

  # Generate today's signals (after backtest has run)
  python main.py signals --strategy trend --ticker ^NSEI

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
import sys
import os
from datetime import date

# ── Setup path ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

import config
from src.data_ingestion import get_combined_dataset, fetch_all_underlyings
from src.backtester import Backtester
from src.reporter import compute_metrics, trades_to_dataframe, print_summary
from src.html_report import generate_html_report
from src.strategies.trend_following import TrendFollowingStrategy
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.combined_strategy import CombinedStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.bhavcopy_downloader import download_range, download_last_n_days, verify_downloads


# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Strategy factory ─────────────────────────────────────────────
def get_strategy(name: str):
    _trend = TrendFollowingStrategy(
        fast_ma              = config.TREND_FAST_MA,
        slow_ma              = config.TREND_SLOW_MA,
        use_adx_filter       = False,
        use_confirm          = False,
        use_direction_filter = False,
        use_time_stop        = False,
    )
    _rsi = RSIStrategy(
        rsi_period     = config.RSI_PERIOD,
        oversold       = config.RSI_OVERSOLD,
        overbought     = config.RSI_OVERBOUGHT,
        confirm_bars   = config.RSI_CONFIRM_BARS,
        time_stop_days = config.RSI_TIME_STOP_DAYS,
        weekly         = True,
    )

    strategies = {
        "trend"    : _trend,
        "trend_raw": TrendFollowingStrategy(
            fast_ma              = config.TREND_FAST_MA,
            slow_ma              = config.TREND_SLOW_MA,
            use_adx_filter       = False,
            use_confirm          = False,
            use_direction_filter = False,
            use_time_stop        = False,
        ),
        "rsi"      : _rsi,
        "combined" : CombinedStrategy([
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
        ]),
        "mean_rev" : MeanReversionStrategy(),
    }
    if name not in strategies:
        print(f"Unknown strategy '{name}'. Available: {list(strategies.keys())}")
        sys.exit(1)
    return strategies[name]


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
    """Generate today's trading signals."""
    tickers = args.ticker or config.UNDERLYINGS
    logger.info(f"Generating signals: {args.strategy} on {tickers}")

    # Use a small recent window for signal generation
    from datetime import timedelta
    end   = date.today()
    start = end - timedelta(days=365)

    data = get_combined_dataset(start=start, end=end, force_refresh=True)
    data = {k: v for k, v in data.items() if k in tickers}

    strategy = get_strategy(args.strategy)
    signals_out = []

    for ticker, df in data.items():
        df.attrs["ticker"] = ticker
        vix = df.get("VIX", None)
        if vix is None:
            continue

        sigs = strategy.generate_signals(df, vix, end)
        for sig in sigs:
            if sig.signal_type != "entry":
                continue
            spot  = float(df["Close"].iloc[-1])
            print(f"\n{'─'*60}")
            print(f"  ACTION     : {'BUY' if sig.direction == 'long' else 'SELL'} {sig.option_type}")
            print(f"  Underlying : {ticker}")
            print(f"  Spot       : ₹{spot:,.2f}")
            print(f"  Strike     : ATM ≈ ₹{round(spot/50)*50:,.0f}")
            print(f"  Expiry     : {sig.expiry}")
            print(f"  Meta       : {sig.meta}")

    print(f"\n{'─'*60}")
    print("  Run above trades manually on Groww before market open.")
    print(f"{'─'*60}\n")


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
    import logging
    # Suppress per-trade INFO logs during optimization runs for clean progress output
    logging.getLogger("src.backtester").setLevel(logging.WARNING)
    logging.getLogger("src.data_ingestion").setLevel(logging.WARNING)
    logging.getLogger("src.options_pricing").setLevel(logging.WARNING)
    logging.getLogger("src.risk_manager").setLevel(logging.WARNING)

    from src.optimizer import run_optimization
    from src.walk_forward import run_walk_forward

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
                           help="Strategy: trend | rsi | combined | mean_rev (default: combined)")
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


if __name__ == "__main__":
    main()
