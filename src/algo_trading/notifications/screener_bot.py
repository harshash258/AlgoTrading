#!/usr/bin/env python3
"""
screener_telegram.py — Send biweekly stock screening results via Telegram.

Reads CSV output from both strategies and sends a formatted summary message.
Includes top matches for each strategy, match counts, and execution details.
"""

import os
import csv
import logging
from datetime import datetime
from pathlib import Path
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REPORTS_DIR = "reports"


def read_screening_results(strategy_name: str) -> dict:
    """
    Read the CSV file for a given strategy and extract top matches.
    
    Returns:
        dict with keys: 'count', 'matches' (list of top 10 dicts)
    """
    # Find the most recent CSV file for this strategy
    pattern = f"screen_{strategy_name}_*.csv"
    csv_files = list(Path(REPORTS_DIR).glob(pattern))
    
    if not csv_files:
        logger.warning(f"No CSV file found for strategy: {strategy_name}")
        return {"count": 0, "matches": [], "file": None}
    
    # Get the most recent file
    csv_file = max(csv_files, key=os.path.getctime)
    logger.info(f"Reading {csv_file}")
    
    matches = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            logger.warning(f"CSV file {csv_file} has no headers")
            return {"count": 0, "matches": [], "file": str(csv_file)}
        
        for row in reader:
            # Include all rows from this strategy's CSV
            # (file is already filtered to this strategy)
            matches.append(row)
    
    logger.info(f"Found {len(matches)} total matches in {csv_file}")
    
    return {
        "count": len(matches),
        "matches": matches[:10],  # Top 10 matches
        "file": str(csv_file)
    }


def format_telegram_message(cheap_to_moon_data: dict, multibagger_data: dict) -> str:
    """Format screening results into a Telegram-friendly message."""
    
    msg = f"*Stock Screener Results* - {datetime.now().strftime('%d %b %Y')}\n\n"
    
    # Cheap to Moon section
    msg += f"*Cheap to Moon*: {cheap_to_moon_data['count']} matches\n"
    if cheap_to_moon_data['matches']:
        msg += "Top matches:\n"
        for i, stock in enumerate(cheap_to_moon_data['matches'][:5], 1):
            ticker = stock.get('ticker', 'N/A')
            price = stock.get('price', 'N/A')
            pb = stock.get('pb', 'N/A')
            pe = stock.get('pe', 'N/A')
            msg += f"{i}. {ticker} - Price: {price}, P/B: {pb}, P/E: {pe}\n"
        if cheap_to_moon_data['count'] > 5:
            msg += f"... and {cheap_to_moon_data['count'] - 5} more\n"
    else:
        msg += "No matches found\n"
    
    msg += "\n"
    
    # Multibagger section
    msg += f"*Multibagger*: {multibagger_data['count']} matches\n"
    if multibagger_data['matches']:
        msg += "Top matches:\n"
        for i, stock in enumerate(multibagger_data['matches'][:5], 1):
            ticker = stock.get('ticker', 'N/A')
            price = stock.get('price', 'N/A')
            pb = stock.get('pb', 'N/A')
            pe = stock.get('pe', 'N/A')
            msg += f"{i}. {ticker} - Price: {price}, P/B: {pb}, P/E: {pe}\n"
        if multibagger_data['count'] > 5:
            msg += f"... and {multibagger_data['count'] - 5} more\n"
    else:
        msg += "No matches found\n"
    
    msg += "\n"
    
    # Summary
    total = cheap_to_moon_data['count'] + multibagger_data['count']
    msg += f"*Total matches*: {total}\n"
    msg += f"_Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}_"
    
    return msg


def send_telegram_message(message: str) -> bool:
    """Send message to Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        logger.info("In GitHub Actions, these are set via secrets")
        logger.info("Locally, set them via environment variables for testing")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Message sent successfully. Response code: {response.status_code}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def main():
    """Main entry point."""
    logger.info("Starting biweekly stock screener summary...")
    
    # Read screening results
    cheap_to_moon_data = read_screening_results("cheap_to_moon")
    multibagger_data = read_screening_results("multibagger")
    
    logger.info(f"Cheap to Moon: {cheap_to_moon_data['count']} matches from {cheap_to_moon_data['file']}")
    logger.info(f"Multibagger: {multibagger_data['count']} matches from {multibagger_data['file']}")
    
    # Format and send message
    message = format_telegram_message(cheap_to_moon_data, multibagger_data)
    
    logger.info("Telegram message prepared:")
    print("\n" + "="*70)
    print(message)
    print("="*70 + "\n")
    
    success = send_telegram_message(message)
    
    if success:
        logger.info("Biweekly screener summary sent successfully")
        return 0
    else:
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            logger.error("Failed to send biweekly screener summary")
            return 1
        else:
            logger.info("Skipped sending (tokens not configured)")
            return 0


if __name__ == "__main__":
    exit(main())


# ─────────────────────────────────────────────────────────────────
# Public API / Wrapper Functions
# ─────────────────────────────────────────────────────────────────

def send_screener_summary() -> bool:
    """
    Send biweekly stock screener summary via Telegram.
    Uses environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
    
    Reads the most recent screening CSV files and sends a formatted summary.
    
    Returns
    -------
    bool
        True if successful or tokens not configured (skip silently)
        False if there was an error during sending
    """
    return main() == 0
