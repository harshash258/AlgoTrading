#!/usr/bin/env python
"""Build real fundamentals CSV for ALL NSE tickers from yfinance data."""

import warnings
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*utcnow.*')

import os
import sys
import time
import logging
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

# Setup logging with immediate flush
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load all tickers from template
logger.info("Loading ticker universe from nse_tickers_template.txt...")
if not os.path.exists('nse_tickers_template.txt'):
    logger.error("nse_tickers_template.txt not found!")
    sys.exit(1)

with open('nse_tickers_template.txt') as f:
    tickers = [line.strip() for line in f if line.strip() and not line.startswith('#')]

total_tickers = len(tickers)
logger.info(f"✓ Loaded {total_tickers} tickers")
logger.info("=" * 70)
logger.info(f"Starting fetch for {total_tickers} NSE stocks...")
logger.info("=" * 70)

os.makedirs('data', exist_ok=True)
csv_path = 'data/fundamentals.csv'

rows = []
success_count = 0
fail_count = 0
start_time = time.time()

for i, ticker in enumerate(tickers, 1):
    t_start = time.time()
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period='1y')
        
        price = info.get('currentPrice')
        if not price or hist.empty:
            fail_count += 1
            logger.warning(f"[{i:4d}/{total_tickers}] {ticker:<14} ⚠ Skipped (no price/history data)")
            continue
        
        market_cap_cr = (info.get('marketCap', 0) / 1_00_00_000) if info.get('marketCap') else None
        pe = info.get('trailingPE')
        pb = info.get('priceToBook')
        eps = info.get('trailingEps')
        roe = info.get('returnOnEquity')
        dividend_yield = info.get('dividendYield')
        
        book_value = (price / pb) if (pb and pb > 0 and price > 0) else None
        
        low_52w = hist['Low'].min() if len(hist) > 0 else None
        high_52w = hist['High'].max() if len(hist) > 0 else None
        up_52w_pct = ((price - low_52w) / low_52w * 100) if (low_52w and low_52w > 0) else None
        
        row = {
            'ticker': ticker,
            'symbol': ticker.replace('.NS', ''),
            'updated_date': datetime.now(timezone.utc).isoformat(),
            'price': price,
            'market_cap_cr': market_cap_cr,
            'eps': eps,
            'book_value': book_value,
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'roce': None,
            'dividend_yield': dividend_yield,
            'debt_to_equity': None,
            'sales_cr': None,
            'profit_growth_3yr': None,
            'sales_growth_3yr': None,
            'promoter_holding': None,
            'pledged_pct': None,
            'error': None,
        }
        
        rows.append(row)
        success_count += 1
        elapsed_stock = time.time() - t_start
        pe_str = f"{pe:.1f}" if pe else "-"
        pb_str = f"{pb:.1f}" if pb else "-"
        logger.info(f"[{i:4d}/{total_tickers}] {ticker:<14} ✓ Price: ₹{price:<8.2f} P/E: {pe_str:<6} P/B: {pb_str:<6} ({elapsed_stock:.2f}s)")
        
        # Periodically save backup every 50 tickers
        if len(rows) % 50 == 0:
            pd.DataFrame(rows).to_csv(csv_path, index=False)
            elapsed = time.time() - start_time
            rate = i / elapsed
            eta = (total_tickers - i) / rate if rate > 0 else 0
            logger.info(f"--- [Progress: {i}/{total_tickers} ({100*i/total_tickers:.1f}%)] | Elapsed: {elapsed/60:.1f}m | ETA: {eta/60:.1f}m | Saved checkpoint ---")

    except Exception as e:
        fail_count += 1
        logger.error(f"[{i:4d}/{total_tickers}] {ticker:<14} ✗ Error: {str(e)}")

total_time = time.time() - start_time
logger.info("=" * 70)
logger.info("FINAL RESULTS:")
logger.info(f"  Total tickers:        {total_tickers}")
logger.info(f"  Successfully fetched: {success_count} ({100*success_count/total_tickers:.1f}%)")
logger.info(f"  Failed:               {fail_count} ({100*fail_count/total_tickers:.1f}%)")
logger.info(f"  Total time:           {total_time:.0f}s ({total_time/60:.1f} min)")
logger.info("=" * 70)

# Final Save
if rows:
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    logger.info(f"✓ Saved {len(rows)} records to {csv_path}")
else:
    logger.error("No data to save - all tickers failed")
