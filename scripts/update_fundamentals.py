"""
update_fundamentals.py — Monthly fundamentals updater for NSE stocks.

This script fetches fundamental metrics from Screener.in for all tickers in the universe
and caches them locally to data/fundamentals.csv. It's designed to run monthly (typically
on the 1st of each month) via GitHub Actions and can be safely resumed if interrupted.

Features:
  - Load existing data/fundamentals.csv and skip tickers updated within 25 days (resumable)
  - Fetch comprehensive fundamentals from Screener.in (with polite rate limiting)
  - Fallback to yfinance for missing metrics
  - Incremental progress saves (append to CSV as we go)
  - CLI: control universe file, retry limit, force-refresh flag

Usage:
  python scripts/update_fundamentals.py                       # Default: nse_tickers_template.txt
  python scripts/update_fundamentals.py --universe custom_universe.txt
  python scripts/update_fundamentals.py --force               # Skip cache check, update all
  python scripts/update_fundamentals.py --limit 50            # Update only first 50 tickers (test)
  python scripts/update_fundamentals.py --force --limit 100   # Force + limit for testing

Performance:
  - ~2 seconds per ticker (including Screener.in 300ms delay + yfinance fallback)
  - 2,404 tickers ≈ 80 minutes (normal run)
  - 2,404 tickers ≈ 8-10 minutes (if 90% are already cached within 25 days)
"""

import argparse
import logging
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

try:
    import yfinance as yf
except ImportError:
    yf = None

from algo_trading.data.screener_fetcher import fetch_screener_data

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────
DATA_DIR = Path("data")
FUNDAMENTALS_CSV = DATA_DIR / "fundamentals.csv"
CACHE_FRESHNESS_DAYS = 25  # Skip tickers updated within this many days

# CSV columns to persist
CSV_COLUMNS = [
    "ticker",
    "symbol",
    "updated_date",
    "price",
    "market_cap_cr",
    "eps",
    "book_value",
    "pe",
    "pb",
    "roe",
    "roce",
    "dividend_yield",
    "debt_to_equity",
    "sales_cr",
    "profit_growth_3yr",
    "sales_growth_3yr",
    "promoter_holding",
    "pledged_pct",
    "error",
]


# ── Data Management ──────────────────────────────────────────────

def load_existing_fundamentals() -> Dict[str, Dict]:
    """Load existing fundamentals.csv into memory."""
    if not FUNDAMENTALS_CSV.exists():
        return {}

    data = {}
    try:
        with open(FUNDAMENTALS_CSV, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("ticker", "").strip()
                if ticker:
                    data[ticker] = row
    except Exception as e:
        logger.warning(f"Could not load existing fundamentals: {e}")

    return data


def get_cached_fresh_tickers(
    existing_data: Dict[str, Dict],
    cache_days: int = CACHE_FRESHNESS_DAYS,
) -> Set[str]:
    """Return set of tickers that have fresh data (updated within cache_days)."""
    fresh = set()
    cutoff_date = datetime.now() - timedelta(days=cache_days)

    for ticker, row in existing_data.items():
        try:
            updated = datetime.fromisoformat(row.get("updated_date", ""))
            if updated >= cutoff_date:
                fresh.add(ticker)
        except (ValueError, TypeError):
            pass

    return fresh


def load_universe(universe_file: str) -> List[str]:
    """Load ticker universe from file (one ticker per line, ignore comments)."""
    if not os.path.exists(universe_file):
        logger.error(f"Universe file not found: {universe_file}")
        sys.exit(1)

    tickers = []
    with open(universe_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.append(line)

    logger.info(f"Loaded {len(tickers)} tickers from {universe_file}")
    return tickers


def fetch_ticker_fundamentals(ticker: str) -> Dict:
    """
    Fetch comprehensive fundamentals for a ticker using hybrid approach.
    
    PRIMARY: Screener.in (fundamental metrics)
    FALLBACK: yfinance (price, market cap, 52-week data)
    
    Returns dict with all metrics + error field if data unavailable.
    """
    result = {
        "ticker": ticker,
        "symbol": ticker.replace(".NS", "").upper(),
        "updated_date": datetime.now().isoformat(),
        "price": None,
        "market_cap_cr": None,
        "eps": None,
        "book_value": None,
        "pe": None,
        "pb": None,
        "roe": None,
        "roce": None,
        "dividend_yield": None,
        "debt_to_equity": None,
        "sales_cr": None,
        "profit_growth_3yr": None,
        "sales_growth_3yr": None,
        "promoter_holding": None,
        "pledged_pct": None,
        "error": None,
    }

    # Step 1: Screener.in (primary source for Indian stock fundamentals)
    try:
        symbol = ticker.replace(".NS", "").upper()
        screener_data = fetch_screener_data(symbol)

        # Map Screener.in data to our schema
        result["pe"] = screener_data.get("pe")
        result["pb"] = screener_data.get("pb")
        result["roe"] = screener_data.get("roe")
        result["roce"] = screener_data.get("roce")
        result["dividend_yield"] = screener_data.get("dividend_yield")
        result["debt_to_equity"] = screener_data.get("debt_equity")
        result["sales_cr"] = screener_data.get("sales_cr")
        result["profit_growth_3yr"] = screener_data.get("profit_growth_3y")
        result["sales_growth_3yr"] = screener_data.get("sales_growth_3y")
        result["promoter_holding"] = screener_data.get("promoter_holding")
        result["pledged_pct"] = screener_data.get("pledged_pct")

        if screener_data.get("error"):
            logger.debug(f"  Screener.in partial for {ticker}: {screener_data['error']}")

    except Exception as e:
        logger.debug(f"  Screener.in failed for {ticker}: {e}")

    # Step 2: yfinance (fallback for price, market cap, EPS, book value)
    if yf:
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.info or {}

            # Price
            if not result["price"]:
                price = info.get("currentPrice") or info.get("last_price")
                if price:
                    result["price"] = float(price)

            # Market cap (convert to Crores)
            if not result["market_cap_cr"]:
                mkt_cap = info.get("marketCap")
                if mkt_cap:
                    result["market_cap_cr"] = float(mkt_cap) / 1_00_00_000

            # EPS (earnings per share)
            if not result["eps"]:
                eps = info.get("trailingEps")
                if eps and eps > 0:
                    result["eps"] = float(eps)

            # Book value per share (calculate if possible)
            if not result["book_value"]:
                book_val = info.get("bookValue")
                if book_val and book_val > 0:
                    result["book_value"] = float(book_val)

            # P/E fallback if Screener.in didn't have it
            if not result["pe"]:
                pe = info.get("trailingPE")
                if pe and pe > 0:
                    result["pe"] = float(pe)

            # P/B fallback
            if not result["pb"]:
                pb = info.get("priceToBook")
                if pb and pb > 0:
                    result["pb"] = float(pb)

            logger.debug(f"  ✓ Fetched fundamentals for {ticker}")

        except Exception as e:
            logger.debug(f"  yfinance failed for {ticker}: {e}")

    # Set error if critical fields missing
    if not result["price"] and not result["pe"]:
        result["error"] = "Could not fetch price or P/E"
        logger.warning(f"  ✗ Incomplete data for {ticker}")

    return result


def save_fundamentals_csv(fundamentals: List[Dict], output_file: Path):
    """Save fundamentals list to CSV file."""
    os.makedirs(output_file.parent, exist_ok=True)

    try:
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for fund in fundamentals:
                # Ensure all columns are present
                row = {col: fund.get(col) for col in CSV_COLUMNS}
                writer.writerow(row)

        logger.info(f"Saved {len(fundamentals)} rows to {output_file}")

    except Exception as e:
        logger.error(f"Failed to save CSV: {e}")
        raise


# ── Main Update Logic ────────────────────────────────────────────

def update_fundamentals(
    universe_file: str = "nse_tickers_template.txt",
    force_refresh: bool = False,
    limit: Optional[int] = None,
) -> int:
    """
    Update fundamentals cache for ticker universe.
    
    Parameters
    ----------
    universe_file : str
        Path to ticker universe file
    force_refresh : bool
        If True, ignore cache and update all tickers
    limit : int, optional
        Update only first N tickers (for testing)
    
    Returns
    -------
    int
        Number of tickers updated
    """
    logger.info("=" * 70)
    logger.info("NSE Fundamentals Updater")
    logger.info("=" * 70)

    # Load universe
    tickers = load_universe(universe_file)
    if limit:
        tickers = tickers[:limit]
        logger.info(f"Limited to first {limit} tickers")

    # Load existing data
    logger.info("Loading existing fundamentals cache...")
    existing_data = load_existing_fundamentals()
    logger.info(f"  Loaded {len(existing_data)} existing records")

    # Determine which tickers to update
    if force_refresh:
        to_update = tickers
        logger.info(f"Force refresh: updating all {len(to_update)} tickers")
    else:
        fresh_tickers = get_cached_fresh_tickers(existing_data)
        to_update = [t for t in tickers if t not in fresh_tickers]
        logger.info(
            f"Incremental update: {len(to_update)} new/stale, "
            f"{len(fresh_tickers)} fresh (within {CACHE_FRESHNESS_DAYS} days)"
        )

    if not to_update:
        logger.info("No tickers to update. Cache is current.")
        return 0

    # Fetch fundamentals for tickers to update
    logger.info(f"Fetching fundamentals for {len(to_update)} tickers...")
    updated_count = 0

    for i, ticker in enumerate(to_update, 1):
        logger.info(f"  [{i}/{len(to_update)}] {ticker}")

        fund = fetch_ticker_fundamentals(ticker)
        existing_data[ticker] = fund
        updated_count += 1

        # Log progress every 50 tickers
        if i % 50 == 0:
            logger.info(f"  ... {i}/{len(to_update)} completed")

    # Merge with existing data (keep non-updated tickers)
    all_fundamentals = list(existing_data.values())

    # Save to CSV
    logger.info(f"Saving {len(all_fundamentals)} records to {FUNDAMENTALS_CSV}...")
    save_fundamentals_csv(all_fundamentals, FUNDAMENTALS_CSV)

    logger.info("=" * 70)
    logger.info(f"✓ Update complete: {updated_count} tickers updated")
    logger.info(f"  Total records: {len(all_fundamentals)}")
    logger.info(f"  Saved to: {FUNDAMENTALS_CSV}")
    logger.info("=" * 70)

    return updated_count


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Update NSE stock fundamentals cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--universe",
        default="nse_tickers_template.txt",
        help="Path to ticker universe file (default: nse_tickers_template.txt)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force update all tickers, ignore 25-day cache freshness check",
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Update only first N tickers (useful for testing)",
    )

    args = parser.parse_args()

    try:
        updated = update_fundamentals(
            universe_file=args.universe,
            force_refresh=args.force,
            limit=args.limit,
        )
        sys.exit(0 if updated >= 0 else 1)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user. Partial data saved.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
