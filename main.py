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
from src.strategies.bollinger_band_strategy import BollingerBandStrategy
from src.strategies.confluence_strategy import ConfluenceStrategy
from src.strategies.inverse_strategy import InverseStrategy
from src.strategies.orb_strategy import ORBStrategy
from src.strategies.long_straddle import LongStraddleStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.gap_fade import GapFadeStrategy
from src.strategies.iron_condor import IronCondorStrategy
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
        "mean_rev"   : MeanReversionStrategy(),
        "bb"         : BollingerBandStrategy(
            bb_period        = config.BB_PERIOD,
            bb_std           = config.BB_STD_MULT,
            atr_period       = config.BB_ATR_PERIOD,
            use_trend_filter = config.BB_USE_TREND_FILTER,
            vix_min          = config.BB_VIX_MIN,
            vix_max          = config.BB_VIX_MAX,
            confirm_bars     = config.BB_CONFIRM_BARS,
            time_stop_days   = config.BB_TIME_STOP_DAYS,
            weekly           = True,
        ),
        "confluence" : ConfluenceStrategy(
            fast_ma          = config.TREND_FAST_MA,
            slow_ma          = config.TREND_SLOW_MA,
            rsi_period       = config.RSI_PERIOD,
            rsi_oversold     = config.RSI_OVERSOLD,
            rsi_overbought   = config.RSI_OVERBOUGHT,
            rsi_lookback     = config.CONFLUENCE_RSI_LOOKBACK,
            rsi_entry_max    = config.CONFLUENCE_RSI_ENTRY_MAX,
            rsi_entry_min    = config.CONFLUENCE_RSI_ENTRY_MIN,
            use_trend_filter = config.CONFLUENCE_USE_TREND_FILTER,
            vix_min          = config.BB_VIX_MIN,
            vix_max          = config.BB_VIX_MAX,
            time_stop_days   = config.CONFLUENCE_TIME_STOP_DAYS,
            weekly           = True,
        ),
        # ── New strategies ────────────────────────────────────────
        "orb"        : ORBStrategy(
            breakout_pct   = config.ORB_BREAKOUT_PCT,
            gap_max_pct    = config.ORB_GAP_MAX_PCT,
            use_adx_filter = config.ORB_USE_ADX_FILTER,
            adx_threshold  = config.ORB_ADX_THRESHOLD,
            time_stop_days = config.ORB_TIME_STOP_DAYS,
            weekly         = True,
        ),
        "straddle"   : LongStraddleStrategy(
            iv_entry_pct   = config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct    = config.STRADDLE_IV_EXIT_PCT,
            otm_delta      = 0.0,  # ATM straddle
            time_stop_days = config.STRADDLE_TIME_STOP_DAYS,
            weekly         = True,
        ),
        "strangle"   : LongStraddleStrategy(
            iv_entry_pct   = config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct    = config.STRADDLE_IV_EXIT_PCT,
            otm_delta      = config.STRADDLE_OTM_DELTA if config.STRADDLE_OTM_DELTA > 0 else 0.25,
            time_stop_days = config.STRADDLE_TIME_STOP_DAYS,
            weekly         = True,
        ),
        "vwap_rev"   : VWAPReversionStrategy(
            vwap_window    = config.VWAP_WINDOW,
            std_mult       = config.VWAP_STD_MULT,
            mode           = "reversion",
            time_stop_days = config.VWAP_TIME_STOP_DAYS,
            weekly         = True,
        ),
        "vwap_brk"   : VWAPReversionStrategy(
            vwap_window    = config.VWAP_WINDOW,
            std_mult       = config.VWAP_STD_MULT,
            mode           = "breakout",
            time_stop_days = config.VWAP_TIME_STOP_DAYS,
            weekly         = True,
        ),
        "gap_fade"   : GapFadeStrategy(
            gap_min_pct       = config.GAP_MIN_PCT,
            gap_max_pct       = config.GAP_MAX_PCT,
            require_no_follow = config.GAP_REQUIRE_NO_FOLLOW,
            trend_filter      = config.GAP_TREND_FILTER,
            time_stop_days    = config.GAP_TIME_STOP_DAYS,
            weekly            = True,
        ),
        "iron_condor": IronCondorStrategy(
            iv_entry_pct         = config.IC_IV_ENTRY_PCT,
            iv_exit_pct          = config.IC_IV_EXIT_PCT,
            body_delta           = config.IC_BODY_DELTA,
            wing_delta           = config.IC_WING_DELTA,
            use_adx_filter       = config.IC_USE_ADX_FILTER,
            adx_choppy_threshold = config.IC_ADX_CHOPPY_THRESHOLD,
            time_stop_days       = config.IC_TIME_STOP_DAYS,
            weekly               = False,  # monthly for more theta
        ),
    }

    # ── Inverse variants — wrap any strategy to flip CE↔PE ───────
    for _key in list(strategies.keys()):
        strategies[f"inv_{_key}"] = InverseStrategy(strategies[_key])
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
    """Generate today's trading signals and print to console."""
    from src.telegram_notify import generate_signal_message

    strategy = get_strategy(args.strategy) if args.strategy != "combined" else None

    print("\nGenerating signals...\n")
    msg = generate_signal_message(strategy=strategy)

    # Strip HTML tags for clean console output
    import re
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
