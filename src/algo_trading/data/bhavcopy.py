"""
bhavcopy_downloader.py — Automated NSE F&O Bhavcopy downloader.

NSE publishes daily option chain data (bhavcopy) for free.
This module downloads them for a date range and stores in data/bhavcopy/.

TWO URL FORMATS (NSE changed format on July 8, 2024):

  Pre-Jul 2024 (legacy):
    https://archives.nseindia.com/content/fo/BhavCopy_DDMMMYYYY.zip
    e.g. BhavCopy_01JAN2023.zip
    Columns: INSTRUMENT, SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP,
             OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, VAL_INLAKH,
             OPEN_INT, CHG_IN_OI, TIMESTAMP

  Post-Jul 2024 (UDiFF):
    https://nsearchives.nseindia.com/content/fo/BhavCopy_DDMMMYYYY.zip
    (Same URL pattern, different schema — columns renamed)

NSE blocks headless requests. We use curl_cffi (already installed via yfinance)
to mimic a real browser session, which handles the TLS fingerprinting NSE uses.

Usage
-----
  python src/bhavcopy_downloader.py --start 2023-01-01 --end 2023-12-31
  python src/bhavcopy_downloader.py --start 2024-01-01 --end 2024-12-31
  python src/bhavcopy_downloader.py --days 30   # last N calendar days
"""

import os
import sys
import time
import logging
import argparse
from datetime import date, timedelta
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
    _USE_CFFI = True
except ImportError:
    import requests as cffi_requests
    _USE_CFFI = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

BHAVCOPY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "bhavcopy")

# Legacy URL (pre Jul 8 2024) — correct NSE archives path
# Format: fo{DDMMMYYYY}bhav.csv.zip inside DERIVATIVES/YYYY/MMM/
LEGACY_URL = "https://archives.nseindia.com/content/historical/DERIVATIVES/{year}/{month}/fo{date}bhav.csv.zip"

# New UDiFF URL (post Jul 8 2024)
UDIFF_URL  = "https://nsearchives.nseindia.com/content/fo/BhavCopy_{date}.zip"

# NSE format cutover date
UDIFF_CUTOVER = date(2024, 7, 8)

# Browser-like headers to bypass NSE anti-bot
HEADERS = {
    "User-Agent"      : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept"          : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language" : "en-US,en;q=0.5",
    "Accept-Encoding" : "gzip, deflate, br",
    "Referer"         : "https://www.nseindia.com/",
    "Connection"      : "keep-alive",
}

# NSE session cookie URL — must hit this first to get cookies
NSE_HOME = "https://www.nseindia.com"


# ── Session setup ────────────────────────────────────────────────

def _make_session():
    """
    Create a session that mimics a real browser.
    Uses curl_cffi (Chrome TLS fingerprint) if available — bypasses NSE's bot detection.
    Falls back to requests with browser headers.
    """
    if _USE_CFFI:
        logger.info("Using curl_cffi (Chrome impersonation) for NSE requests")
        session = cffi_requests.Session(impersonate="chrome120")
        session.headers.update({
            "Referer"        : "https://www.nseindia.com/",
            "Accept-Language": "en-US,en;q=0.9",
        })
    else:
        import requests as _req
        session = _req.Session()
        session.headers.update(HEADERS)
        logger.info("Using requests with browser headers")

    # Warm up session with NSE homepage to get cookies
    try:
        logger.info("Initialising NSE session...")
        resp = session.get(NSE_HOME, timeout=15)
        if resp.status_code == 200:
            logger.info(f"NSE session ready. Cookies: {list(session.cookies.keys())}")
        else:
            logger.warning(f"NSE homepage returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"Could not initialise NSE session: {e}")

    return session


# ── Date helpers ─────────────────────────────────────────────────

def _fmt_date(d: date) -> str:
    """Format date as DDMMMYYYY (e.g. 01JAN2023) for NSE URL."""
    return d.strftime("%d%b%Y").upper()


def _url_for(d: date) -> str:
    """Return the correct bhavcopy URL for a given date."""
    fmt   = _fmt_date(d)              # e.g. 15JAN2024
    year  = d.strftime("%Y")          # e.g. 2024
    month = d.strftime("%b").upper()  # e.g. JAN
    if d >= UDIFF_CUTOVER:
        return UDIFF_URL.format(date=fmt)
    return LEGACY_URL.format(year=year, month=month, date=fmt)


def _is_weekday(d: date) -> bool:
    """NSE is closed on weekends (and holidays, but we can't know those upfront)."""
    return d.weekday() < 5


def _trading_days(start: date, end: date) -> list[date]:
    """Return all weekdays between start and end inclusive."""
    days = []
    current = start
    while current <= end:
        if _is_weekday(current):
            days.append(current)
        current += timedelta(days=1)
    return days


# ── Download ─────────────────────────────────────────────────────

def download_bhavcopy(
    d: date,
    session,
    output_dir: str = BHAVCOPY_DIR,
    overwrite: bool = False,
) -> str | None:
    """
    Download bhavcopy for a single date.

    Returns the local file path if successful, None if skipped or failed.
    """
    os.makedirs(output_dir, exist_ok=True)
    # Use a consistent local filename regardless of URL format
    fname    = f"fo{_fmt_date(d)}bhav.csv.zip"
    filepath = os.path.join(output_dir, fname)

    if os.path.exists(filepath) and not overwrite:
        logger.debug(f"  {d}: already exists, skipping")
        return filepath

    url = _url_for(d)
    try:
        resp = session.get(url, timeout=30)

        if resp.status_code == 404:
            # Holiday or non-trading day — normal, not an error
            logger.debug(f"  {d}: 404 (likely holiday/non-trading day)")
            return None

        resp.raise_for_status()

        content = resp.content

        # Validate it's a real ZIP (PK magic bytes)
        if not content[:2] == b"PK":
            logger.warning(f"  {d}: Response is not a ZIP file ({len(content)} bytes). Skipping.")
            return None

        with open(filepath, "wb") as f:
            f.write(content)

        size_kb = len(content) / 1024
        logger.info(f"  {d}: Downloaded {fname} ({size_kb:.1f} KB)")
        return filepath

    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            logger.warning(f"  {d}: 403 Forbidden — NSE blocked the request. Waiting 10s...")
            time.sleep(10)
        else:
            logger.warning(f"  {d}: Failed — {e}")
        return None


def download_range(
    start: date,
    end: date,
    output_dir: str = BHAVCOPY_DIR,
    overwrite: bool = False,
    delay_sec: float = 1.5,
) -> dict:
    """
    Download bhavcopy files for all trading days in [start, end].

    Parameters
    ----------
    start      : Start date (inclusive)
    end        : End date (inclusive)
    output_dir : Directory to save ZIP files
    overwrite  : Re-download even if file exists
    delay_sec  : Seconds to wait between requests (be polite to NSE)

    Returns
    -------
    dict with keys: downloaded, skipped, failed, total_days
    """
    days     = _trading_days(start, end)
    session  = _make_session()
    results  = {"downloaded": 0, "skipped": 0, "failed": 0, "total_days": len(days)}

    logger.info(f"Downloading bhavcopy: {start} → {end} ({len(days)} trading days)")
    logger.info(f"Output: {os.path.abspath(output_dir)}")

    for i, d in enumerate(days):
        fname = f"fo{_fmt_date(d)}bhav.csv.zip"
        if os.path.exists(os.path.join(output_dir, fname)) and not overwrite:
            results["skipped"] += 1
            continue

        result = download_bhavcopy(d, session, output_dir, overwrite)

        if result:
            results["downloaded"] += 1
        else:
            results["failed"] += 1

        # Progress every 20 files
        if (i + 1) % 20 == 0:
            logger.info(
                f"Progress: {i+1}/{len(days)} | "
                f"Downloaded: {results['downloaded']} | "
                f"Failed: {results['failed']}"
            )

        # Polite delay — don't hammer NSE
        if i < len(days) - 1:
            time.sleep(delay_sec)

        # Refresh session every 50 requests (cookies expire)
        if (i + 1) % 50 == 0:
            logger.info("Refreshing NSE session...")
            session = _make_session()
            time.sleep(3)

    logger.info(
        f"\nDone. Downloaded: {results['downloaded']} | "
        f"Skipped (exist): {results['skipped']} | "
        f"Failed/Holiday: {results['failed']} | "
        f"Total days: {results['total_days']}"
    )
    return results


def download_last_n_days(n: int = 30, output_dir: str = BHAVCOPY_DIR) -> dict:
    """Download bhavcopy for the last N calendar days."""
    end   = date.today() - timedelta(days=1)  # yesterday (today not published yet)
    start = end - timedelta(days=n)
    return download_range(start, end, output_dir)


# ── Verify downloaded files ───────────────────────────────────────

def verify_downloads(output_dir: str = BHAVCOPY_DIR) -> None:
    """
    Quick check on all downloaded files — count rows and report any corrupt ZIPs.
    """
    import zipfile
    files = sorted(Path(output_dir).glob("*.zip"))
    if not files:
        print(f"No ZIP files found in {output_dir}")
        return

    print(f"\nVerifying {len(files)} bhavcopy files in {output_dir}:\n")
    ok, bad = 0, 0
    for f in files:
        try:
            with zipfile.ZipFile(f) as z:
                names = z.namelist()
                ok += 1
                if ok <= 3 or ok == len(files):  # show first 3 and last
                    print(f"  ✓ {f.name} — {names}")
        except Exception as e:
            print(f"  ✗ {f.name} — CORRUPT: {e}")
            bad += 1

    print(f"\nResult: {ok} OK, {bad} corrupt out of {len(files)} files\n")


# ── Update config ─────────────────────────────────────────────────

def activate_bhavcopy_in_config() -> None:
    """Set BHAVCOPY_FOLDER in config.py to the download directory."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.py")
    abs_dir = os.path.abspath(BHAVCOPY_DIR).replace("\\", "/")

    with open(config_path, "r") as f:
        content = f.read()

    if 'BHAVCOPY_FOLDER       = None' in content:
        content = content.replace(
            'BHAVCOPY_FOLDER       = None          # e.g. "data/bhavcopy"',
            f'BHAVCOPY_FOLDER       = "data/bhavcopy"  # activated by downloader',
        )
        with open(config_path, "w") as f:
            f.write(content)
        print(f"config.py updated: BHAVCOPY_FOLDER = 'data/bhavcopy'")
    else:
        print("config.py already has BHAVCOPY_FOLDER set.")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Download NSE F&O Bhavcopy files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start",    help="Start date YYYY-MM-DD")
    parser.add_argument("--end",      help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days",     type=int, help="Download last N calendar days")
    parser.add_argument("--dir",      default=BHAVCOPY_DIR, help="Output directory")
    parser.add_argument("--overwrite",action="store_true",  help="Re-download existing files")
    parser.add_argument("--verify",   action="store_true",  help="Verify downloaded ZIPs only")
    parser.add_argument("--delay",    type=float, default=1.5, help="Delay between requests (sec)")
    args = parser.parse_args()

    if args.verify:
        verify_downloads(args.dir)
        return

    if args.days:
        results = download_last_n_days(args.days, args.dir)
    elif args.start:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
        results = download_range(start, end, args.dir, args.overwrite, args.delay)
    else:
        # Default: download from 2019 to today (full backtest range)
        print("No date range specified. Downloading full backtest range (2019–today).")
        print("This will take a while (~10-15 minutes for 5 years). Use --days 30 for a quick test.")
        confirm = input("Continue? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted.")
            return
        results = download_range(config.BACKTEST_START, date.today() - timedelta(days=1), args.dir, args.overwrite, args.delay)

    if results["downloaded"] > 0:
        activate_bhavcopy_in_config()
        print(f"\nFiles saved to: {os.path.abspath(args.dir)}")
        print("Run backtest again to use real option premiums.")


if __name__ == "__main__":
    main()
