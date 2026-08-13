"""
Fetches the full list of NSE-listed equity symbols and prints them to
stdout as comma-separated Yahoo-Finance-style tickers, e.g.:
PAGEIND.NS, LTIM.NS, RELIANCE.NS, ...

NSE publishes the master equity list as a CSV at:
https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv

NSE's servers reject requests without browser-like headers and require
cookies from an initial visit to the site, so the script first "warms up"
a session against nseindia.com before hitting the CSV endpoint.

Usage:
  pip install requests
  python fetch_nse_symbols.py
  python fetch_nse_symbols.py > nse_symbols.txt   # to redirect to a file
"""

import sys
import requests

NSE_HOME_URL = "https://www.nseindia.com"
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


def fetch_nse_symbols() -> list[str]:
    """Fetch the NSE equity list and return a list of raw symbols."""
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: hit the homepage first to obtain valid cookies.
    print("Warming up session...", file=sys.stderr)
    session.get(NSE_HOME_URL, timeout=10)

    # Step 2: fetch the equity list CSV using the warmed-up session.
    print("Fetching NSE equity list...", file=sys.stderr)
    response = session.get(NSE_EQUITY_LIST_URL, timeout=15)
    response.raise_for_status()

    lines = response.text.splitlines()
    header = lines[0].split(",")
    symbol_idx = header.index("SYMBOL")
    symbols = [
        line.split(",")[symbol_idx].strip()
        for line in lines[1:]
        if line.strip()
    ]
    return symbols


def save_symbols_to_file(symbols: list[str], filename: str) -> None:
    """Save symbols to a file as one ticker per line with .NS suffix."""
    with open(filename, 'w') as f:
        f.write("# NSE Stock Universe - Auto-generated from NSE master list\n")
        f.write("# Total stocks: {}\n".format(len(symbols)))
        f.write("# Last updated: 2026-08-13\n")
        f.write("# Usage: python main.py screen --strategy hidden_gem --universe nse_tickers_template.txt\n\n")
        for sym in symbols:
            f.write(f"{sym}.NS\n")


def main():
    try:
        symbols = fetch_nse_symbols()
    except requests.RequestException as exc:
        print(f"Failed to fetch NSE data: {exc}", file=sys.stderr)
        print(
            "NSE occasionally blocks automated requests. If this keeps "
            "failing, try again after a few seconds, or open "
            "https://www.nseindia.com in a browser first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not symbols:
        print(
            "No symbols found in the response. NSE may have changed its format.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"✅ Successfully fetched {len(symbols)} NSE stocks", file=sys.stderr)
    
    # Save to file
    save_symbols_to_file(symbols, "nse_tickers_template.txt")
    print(f"✅ Saved to nse_tickers_template.txt", file=sys.stderr)


if __name__ == "__main__":
    main()
