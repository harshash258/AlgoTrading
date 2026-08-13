"""
download_bhavcopy.py — Automated NSE F&O Bhavcopy downloader.

Downloads daily F&O bhavcopy files from NSE archives for a date range
and saves them to data/bhavcopy/.

NSE URL patterns:
  Pre-Jul 2024  (legacy ZIP):
    https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip
    e.g. fo01JAN2023bhav.csv.zip

  Post-Jul 2024 (UDiFF format):
    https://nsearchives.nseindia.com/content/fo/BhavCopy_FOO_DDMMYYYY_F_0000000.csv.zip
    e.g. BhavCopy_FOO_01012025_F_0000000.csv.zip

Usage:
    python download_bhavcopy.py                         # downloads 2019-01-01 to today
    python download_bhavcopy.py --start 2022-01-01      # custom start
    python download_bhavcopy.py --start 2022-01-01 --end 2022-12-31
    python download_bhavcopy.py --year 2023             # full year
"""

import os
import sys
import time
import argparse
import logging
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), "..", "data", "bhavcopy")
CUTOVER_DATE = date(2024, 7, 8)   # NSE switched to UDiFF on this date

# Legacy format (pre Jul 2024)
LEGACY_URL = (
    "https://nsearchives.nseindia.com/content/historical/DERIVATIVES"
    "/{year}/{mon}/fo{dd}{mon}{year}bhav.csv.zip"
)

# UDiFF format (post Jul 2024)
UDIFF_URL = (
    "https://nsearchives.nseindia.com/content/fo"
    "/BhavCopy_FOO_{dd}{mm}{yyyy}_F_0000000.csv.zip"
)

MONTH_ABBR = {
    1:"JAN", 2:"FEB", 3:"MAR", 4:"APR",
    5:"MAY", 6:"JUN", 7:"JUL", 8:"AUG",
    9:"SEP", 10:"OCT", 11:"NOV", 12:"DEC",
}

# NSE requires browser-like headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept"         : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer"        : "https://www.nseindia.com/",
}

# ── URL builders ─────────────────────────────────────────────────

def build_url(d: date) -> str:
    """Return the correct download URL for a given date."""
    dd  = f"{d.day:02d}"
    mm  = f"{d.month:02d}"
    mon = MONTH_ABBR[d.month]
    yyyy = str(d.year)

    if d < CUTOVER_DATE:
        return LEGACY_URL.format(dd=dd, mon=mon, year=yyyy)
    else:
        return UDIFF_URL.format(dd=dd, mm=mm, yyyy=yyyy)


def output_filename(d: date) -> str:
    """Local filename to save the downloaded file."""
    return f"fo_bhavcopy_{d.strftime('%Y%m%d')}.zip"


# ── Downloader ───────────────────────────────────────────────────

def download_one(d: date, session: requests.Session, force: bool = False) -> bool:
    """
    Download bhavcopy for one date. Returns True on success.
    Skips weekends automatically.
    Skips if file already exists (unless force=True).
    """
    # Skip weekends
    if d.weekday() >= 5:
        return False

    # Skip future dates
    if d > date.today():
        return False

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, output_filename(d))

    if os.path.exists(out_path) and not force:
        logger.debug(f"Already exists: {output_filename(d)}")
        return True

    url = build_url(d)

    try:
        resp = session.get(url, timeout=30, headers=HEADERS)

        if resp.status_code == 200 and len(resp.content) > 1000:
            with open(out_path, "wb") as f:
                f.write(resp.content)
            size_kb = len(resp.content) / 1024
            logger.info(f"Downloaded: {output_filename(d)} ({size_kb:.0f} KB)")
            return True
        elif resp.status_code == 404:
            # Holiday or exchange closed — not an error
            logger.debug(f"Not found (holiday?): {d.isoformat()}")
            return False
        else:
            logger.warning(f"HTTP {resp.status_code} for {d.isoformat()}: {url}")
            return False

    except requests.RequestException as e:
        logger.warning(f"Request failed for {d.isoformat()}: {e}")
        return False


def download_range(
    start: date,
    end: date,
    force: bool = False,
    delay: float = 0.5,
) -> tuple[int, int]:
    """
    Download bhavcopy for all trading days in [start, end].

    Returns (downloaded_count, skipped_count).
    delay: seconds between requests (be polite to NSE servers).
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Warm up session with NSE homepage (sets cookies)
    try:
        logger.info("Warming up NSE session...")
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
    except Exception:
        logger.warning("Could not reach NSE homepage — continuing anyway")

    downloaded, skipped, failed = 0, 0, 0
    current = start
    total_days = (end - start).days + 1

    logger.info(f"Downloading F&O bhavcopy: {start} → {end}")
    logger.info(f"Output folder: {OUTPUT_DIR}")

    while current <= end:
        if current.weekday() < 5:  # Mon–Fri only
            out_path = os.path.join(OUTPUT_DIR, output_filename(current))
            if os.path.exists(out_path) and not force:
                skipped += 1
            else:
                success = download_one(current, session, force)
                if success:
                    downloaded += 1
                    time.sleep(delay)
                else:
                    failed += 1
        current += timedelta(days=1)

    logger.info(
        f"\nDone. Downloaded: {downloaded} | "
        f"Already had: {skipped} | "
        f"Not found/failed: {failed}"
    )
    return downloaded, skipped


# ── Config update ────────────────────────────────────────────────

def update_config_bhavcopy_folder():
    """Update BHAVCOPY_FOLDER in config.py to point to data/bhavcopy."""
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    if 'BHAVCOPY_FOLDER       = None' in content:
        content = content.replace(
            'BHAVCOPY_FOLDER       = None          # e.g. "data/bhavcopy"',
            'BHAVCOPY_FOLDER       = "data/bhavcopy"  # enabled after download',
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("config.py updated: BHAVCOPY_FOLDER = 'data/bhavcopy'")
    else:
        logger.info("config.py already has BHAVCOPY_FOLDER set")


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download NSE F&O Bhavcopy files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--start", default=config.BACKTEST_START.isoformat(),
        help=f"Start date YYYY-MM-DD (default: {config.BACKTEST_START})"
    )
    parser.add_argument(
        "--end", default=date.today().isoformat(),
        help=f"End date YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Download a full year (overrides --start/--end)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if file already exists"
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Seconds between requests (default: 0.5)"
    )
    parser.add_argument(
        "--no-config-update", action="store_true",
        help="Don't update BHAVCOPY_FOLDER in config.py after download"
    )
    args = parser.parse_args()

    if args.year:
        start = date(args.year, 1, 1)
        end   = date(args.year, 12, 31)
    else:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end)

    if start > end:
        print(f"Error: start ({start}) must be before end ({end})")
        sys.exit(1)

    downloaded, skipped = download_range(
        start, end,
        force=args.force,
        delay=args.delay,
    )

    # Update config if we actually downloaded something new
    if downloaded > 0 and not args.no_config_update:
        update_config_bhavcopy_folder()

    # Show what we have
    existing = [
        f for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".zip")
    ] if os.path.exists(OUTPUT_DIR) else []

    print(f"\n{'='*50}")
    print(f"  Bhavcopy files in {OUTPUT_DIR}: {len(existing)}")
    if existing:
        dates = sorted(existing)
        print(f"  Range: {dates[0]} → {dates[-1]}")
    print(f"{'='*50}")
    print("\nNext step: run your backtest with real options data:")
    print("  python main.py backtest --strategy combined")


if __name__ == "__main__":
    main()
