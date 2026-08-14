#!/usr/bin/env python
"""Build real fundamentals CSV for ALL NSE tickers from yfinance data."""

import warnings
warnings.filterwarnings('ignore', category=FutureWarning)  # Suppress yfinance pandas deprecation warnings

import yfinance as yf
import pandas as pd
from datetime import datetime
import sys
import time
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load all tickers from template
logger.info("Loading ticker universe from nse_tickers_template.txt...")
with open('nse_tickers_template.txt') as f:
    tickers = [line.strip() for line in f if line.strip() and not line.startswith('#')]

logger.info(f"✓ Loaded {len(tickers)} tickers")
logger.info("="*70)
logger.info(f"Starting fetch for {len(tickers)} NSE stocks...")
logger.info("="*70)

rows = []
success_count = 0
fail_count = 0
start_time = time.time()

for i, ticker in enumerate(tickers, 1):
    try:
        # Fetch ticker info
        t = yf.Ticker(ticker)
        info = t.info or {}
        
        # Fetch historical data for 52-week range
        hist = t.history(period='1y')
        
        price = info.get('currentPrice')
        if not price or (hist.empty):
            fail_count += 1
            logger.debug(f"  SKIP [{i:4d}/{len(tickers)}] {ticker} - no price data")
            if i % 100 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed
                eta = (len(tickers) - i) / rate if rate > 0 else 0
                logger.info(f"  Progress: {i}/{len(tickers)} ({100*i/len(tickers):.1f}%) | Time: {elapsed:.0f}s | ETA: {eta:.0f}s | Success: {success_count}")
            continue
        
        # Extract data
        market_cap_cr = info.get('marketCap', 0) / 1_00_00_000 if info.get('marketCap') else None
        pe = info.get('trailingPE')
        pb = info.get('priceToBook')
        eps = info.get('trailingEps')
        roe = info.get('returnOnEquity')
        dividend_yield = info.get('dividendYield')
        
        # Book value per share
        book_value = None
        if pb and pb > 0 and price > 0:
            book_value = price / pb
        
        # 52-week range
        low_52w = hist['Low'].min() if len(hist) > 0 else None
        high_52w = hist['High'].max() if len(hist) > 0 else None
        up_52w_pct = ((price - low_52w) / low_52w * 100) if (low_52w and low_52w > 0) else None
        
        row = {
            'ticker': ticker,
            'symbol': ticker.replace('.NS', ''),
            'updated_date': datetime.now().isoformat(),
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
        logger.debug(f"  ✓ [{i:4d}/{len(tickers)}] {ticker} - price={price:.2f}, market_cap={market_cap_cr:.2f}Cr, P/E={pe}, P/B={pb}")
        
        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed
            eta = (len(tickers) - i) / rate if rate > 0 else 0
            logger.info(f"  Progress: [{i:4d}/{len(tickers)}] {100*i/len(tickers):.1f}% | Elapsed: {elapsed:.0f}s | ETA: {eta:.0f}s | Success: {success_count}")
        
    except Exception as e:
        fail_count += 1
        logger.error(f"  ✗ [{i:4d}/{len(tickers)}] {ticker} - Error: {str(e)}")
        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed
            eta = (len(tickers) - i) / rate if rate > 0 else 0
            logger.info(f"  Progress: [{i:4d}/{len(tickers)}] {100*i/len(tickers):.1f}% | Elapsed: {elapsed:.0f}s | ETA: {eta:.0f}s | Success: {success_count}")

total_time = time.time() - start_time
logger.info("="*70)
logger.info(f"FINAL RESULTS:")
logger.info(f"  Total tickers:     {len(tickers)}")
logger.info(f"  Successfully fetched: {success_count} ({100*success_count/len(tickers):.1f}%)")
logger.info(f"  Failed:            {fail_count} ({100*fail_count/len(tickers):.1f}%)")
logger.info(f"  Total time:        {total_time:.0f}s ({total_time/60:.1f} min)")
logger.info("="*70)

# Save to CSV
if rows:
    df = pd.DataFrame(rows)
    df.to_csv('data/fundamentals.csv', index=False)
    logger.info(f"✓ Saved {len(rows)} records to data/fundamentals.csv")
    logger.info(f"✓ CSV file is ready for stock screener")
else:
    logger.error("No data to save - all tickers failed")
