"""
screener_in_fetcher.py — Fetch comprehensive stock data from Screener.in

Screener.in provides detailed fundamental data for Indian stocks including:
- Valuation: P/E, P/B, Market Cap, Dividend Yield
- Profitability: ROE, ROCE, OPM, PAT
- Growth: Sales Growth, Profit Growth (3Y, 5Y, 10Y)
- Debt: D/E Ratio
- Insider: Promoter Holding, Pledged %

This is much more reliable than yfinance for Indian stocks.
"""

import requests
import logging
import re
from typing import Dict, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SCREENER_IN_BASE = "https://www.screener.in/company/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _extract_metric(soup, keyword: str, fallback_keywords: list = None) -> Optional[float]:
    """Extract a metric value from the page by searching for keyword."""
    keywords_to_try = [keyword] + (fallback_keywords or [])
    
    for kw in keywords_to_try:
        # Find text containing the keyword
        elements = soup.find_all(string=lambda text: kw.lower() in text.lower() if text else False)
        
        for elem in elements:
            # Look for numbers near this element
            parent = elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text(strip=True)
                # Try to extract a number from the parent text
                numbers = re.findall(r'-?\d+\.?\d*%?', text)
                if numbers:
                    try:
                        val = numbers[0].replace('%', '')
                        return float(val)
                    except:
                        pass
                parent = parent.parent
    
    return None


def fetch_screener_data(symbol: str) -> Dict:
    """
    Fetch fundamental data from Screener.in for an Indian stock.
    
    Args:
        symbol: Stock symbol (e.g., 'RELIANCE', 'TCS', 'INFY', 'WIPRO')
    
    Returns:
        Dictionary with comprehensive metrics:
        - price: Current stock price
        - market_cap_cr: Market cap in Crores
        - pe: P/E ratio (trailing)
        - pb: P/B ratio
        - dividend_yield: Dividend yield %
        - roe: ROE %
        - roce: ROCE %
        - debt_equity: Debt/Equity ratio
        - sales_cr: Annual sales in Crores
        - profit_growth_3y: 3-year profit CAGR %
        - sales_growth_3y: 3-year sales CAGR %
        - promoter_holding: Promoter stake %
        - pledged_pct: Pledged shares %
    """
    result = {
        "symbol": symbol,
        "price": None,
        "market_cap_cr": None,
        "pe": None,
        "pb": None,
        "dividend_yield": None,
        "roe": None,
        "roce": None,
        "debt_equity": None,
        "sales_cr": None,
        "profit_growth_3y": None,
        "sales_growth_3y": None,
        "promoter_holding": None,
        "pledged_pct": None,
        "error": None,
    }

    try:
        url = f"{SCREENER_IN_BASE}{symbol.lower()}/consolidated/"
        logger.info(f"Fetching {url}...")
        
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract metrics using multiple methods
        
        # 1. Try to find key metrics in tables
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)

                    # Parse value (remove commas, special chars)
                    try:
                        clean_val = value.replace(',', '').replace('%', '').strip()
                        
                        if 'price' in label and ('current' in label or 'last' in label):
                            result['price'] = float(clean_val.split()[0])
                        elif 'pe' in label and 'p/e' in label:
                            result['pe'] = float(clean_val.split()[0])
                        elif 'p/b' in label:
                            result['pb'] = float(clean_val.split()[0])
                        elif 'roe' in label and '%' in value:
                            result['roe'] = float(clean_val.split()[0])
                        elif 'roce' in label and '%' in value:
                            result['roce'] = float(clean_val.split()[0])
                        elif 'market cap' in label or 'mcap' in label:
                            # Extract number
                            nums = re.findall(r'\d+', clean_val.replace(',', ''))
                            if nums:
                                result['market_cap_cr'] = int(nums[0]) if len(nums[0]) <= 6 else float(nums[0])
                        elif 'dividend' in label and 'yield' in label:
                            result['dividend_yield'] = float(clean_val.split()[0])
                        elif 'd/e' in label or 'debt/equity' in label:
                            result['debt_equity'] = float(clean_val.split()[0])
                        elif 'promoter' in label and '%' in value:
                            result['promoter_holding'] = float(clean_val.split()[0])
                        elif 'pledged' in label:
                            result['pledged_pct'] = float(clean_val.split()[0])
                        elif 'sales' in label and 'cr' in label.lower():
                            # Annual sales in Crores
                            nums = re.findall(r'\d+', clean_val.replace(',', ''))
                            if nums:
                                result['sales_cr'] = float(nums[0])
                            
                    except (ValueError, IndexError):
                        pass

        # 2. Extract growth rates
        for table in tables:
            text = table.get_text()
            if '3 years' in text.lower() or '3year' in text.lower():
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True).lower()
                        value = cells[1].get_text(strip=True)
                        
                        try:
                            val = float(value.replace('%', '').replace(',', '').strip())
                            if 'profit' in label:
                                result['profit_growth_3y'] = val
                            elif 'sales' in label:
                                result['sales_growth_3y'] = val
                        except (ValueError, IndexError):
                            pass

        # If no error occurred, we have data
        if result.get('pe') or result.get('roe') or result.get('roce'):
            logger.info(f"✓ Fetched data for {symbol}")
        else:
            logger.warning(f"Limited data fetched for {symbol}")

    except requests.RequestException as e:
        result["error"] = f"Network error: {str(e)}"
        logger.warning(f"Error fetching {symbol} from Screener.in: {e}")
    except Exception as e:
        result["error"] = f"Parsing error: {str(e)}"
        logger.warning(f"Error parsing data for {symbol}: {e}")

    return result


def test_fetch():
    """Test the screener.in fetcher with a few stocks."""
    test_symbols = ['RELIANCE', 'TCS', 'INFY', 'WIPRO']
    
    print("Testing Screener.in fetcher...\n")
    for symbol in test_symbols:
        data = fetch_screener_data(symbol)
        print(f"{symbol}:")
        for key, value in data.items():
            if value is not None and key != 'error':
                print(f"  {key}: {value}")
        print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_fetch()
