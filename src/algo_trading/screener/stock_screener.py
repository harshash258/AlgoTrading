"""
stock_screener.py — Multi-strategy stock screener for NSE stocks.

Uses hybrid data sources:
  - PRIMARY: Screener.in (best fundamental data for Indian stocks)
  - FALLBACK: yfinance (as backup)

Allows defining multiple screening strategies with different criteria.
Searches across a universe of NSE stocks and returns matches for each strategy.

Strategies:
  - Cheap to Moon: Low P/B, small-to-mid cap with upside
  - Value Play: Low P/E, reasonable P/B
  - GARP: Growth at reasonable price
  - High ROCE: Large-cap quality stocks
  - Hidden Gem: Mid-cap with good valuations

Usage:
  python main.py screen --strategy cheap_to_moon --ticker RELIANCE.NS --ticker TCS.NS
  python main.py screen --strategy hidden_gem --universe nse_tickers.txt
  python main.py screen --strategy cheap_to_moon --strategy high_roce --ticker STOCK.NS
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, List, Any
import time

try:
    import yfinance as yf
except ImportError:
    yf = None

import config

# Import Screener.in fetcher
from algo_trading.data.screener_fetcher import fetch_screener_data

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Strategy Definition
# ─────────────────────────────────────────────────────────────────

@dataclass
class ScreeningStrategy:
    """Define a stock screening strategy with rules."""
    name: str
    rules: Dict[str, Dict[str, float]]
    label: str = ""
    description: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.name

    def validate_stock(self, metrics: Dict[str, Any]) -> tuple[bool, List[str]]:
        """Check if stock passes all rules. Returns (passes, reasons_list)."""
        passes = True
        reasons = []

        for metric_name, rule in self.rules.items():
            value = metrics.get(metric_name)

            if value is None:
                reasons.append(f"✗ {metric_name}: data unavailable")
                passes = False
                continue

            if "min" in rule:
                min_val = rule["min"]
                if value >= min_val:
                    reasons.append(f"✓ {metric_name}: {value:.2f} >= {min_val}")
                else:
                    reasons.append(f"✗ {metric_name}: {value:.2f} < {min_val}")
                    passes = False

            if "max" in rule:
                max_val = rule["max"]
                if value <= max_val:
                    reasons.append(f"✓ {metric_name}: {value:.2f} <= {max_val}")
                else:
                    reasons.append(f"✗ {metric_name}: {value:.2f} > {max_val}")
                    passes = False

        return passes, reasons


# ─────────────────────────────────────────────────────────────────
# Pre-defined Strategies
# ─────────────────────────────────────────────────────────────────

STRATEGY_CHEAP_TO_MOON = ScreeningStrategy(
    name="Cheap to Moon",
    label="🚀 Cheap to Moon",
    description="Ultra-cheap fundamentals with strong growth & momentum - P/B < 1.0, Price < 100, ROCE > 5%",
    rules={
        "pb": {"max": 1.0},  # P/B < 1.0 (severely undervalued)
        "pe": {"min": 0.5},  # P/E > 0.5 (avoid negative/problematic earnings)
        "price": {"max": 100},  # Price < 100 (penny stocks to small-caps)
        "up_52w_pct": {"min": 10},  # Up from 52w low > 10% (momentum)
        "market_cap_cr": {"min": 20},  # Market Cap > 20 Cr (viable size)
        "roce": {"min": 5},  # ROCE > 5% (basic profitability)
    }
)

STRATEGY_MULTIBAGGER = ScreeningStrategy(
    name="Multibagger",
    label="� Multibagger",
    description="High-growth mid-cap stocks with strong fundamentals & promoter backing",
    rules={
        "market_cap_cr": {"min": 100, "max": 2500},  # Mid-cap range (100-2500 Cr)
        "sales_cr": {"min": 100},  # Sales > 100 Cr
        "sales_growth_3yr": {"min": 15},  # Sales growth 3Y > 15%
        "profit_growth_3yr": {"min": 15},  # Profit growth 3Y > 15%
        "roce": {"min": 15},  # ROCE > 15%
        "roe": {"min": 15},  # ROE > 15%
        "debt_to_equity": {"max": 0.5},  # Debt/Equity < 0.5
        "peg_ratio": {"max": 1.5},  # PEG < 1.5
        "promoter_holding": {"min": 40},  # Promoter holding > 40%
        "pledged_pct": {"max": 5},  # Pledged % < 5%
        "up_52w_pct": {"min": 10},  # Up from 52w low > 10%
    }
)

# Registry of all available strategies
ALL_STRATEGIES = {
    "cheap_to_moon": STRATEGY_CHEAP_TO_MOON,
    "multibagger": STRATEGY_MULTIBAGGER,
}


# ─────────────────────────────────────────────────────────────────
# Data Fetching
# ─────────────────────────────────────────────────────────────────

def _fetch_stock_metrics(ticker: str) -> Dict[str, Any]:
    """
    Fetch comprehensive metrics for a stock using HYBRID approach.
    
    PRIMARY: Screener.in (best fundamental data for Indian stocks)
    FALLBACK: yfinance (backup for price/market cap)
    """
    result = {
        "ticker": ticker,
        "price": None,
        "market_cap_cr": None,
        "sales_cr": None,
        "pe": None,
        "pb": None,
        "roe": None,
        "roce": None,
        "price_52w_low": None,
        "price_52w_high": None,
        "up_52w_pct": None,
        "revenue_growth": None,
        "profit_growth": None,
        "debt_to_equity": None,
        "sales_growth_multiple": None,
        "sales_growth_3yr": None,
        "profit_growth_3yr": None,
        "peg_ratio": None,
        "promoter_holding": None,
        "pledged_pct": None,
        "dividend_yield": None,
        "quarterly_sales_growth": None,  # Latest Q / Previous year Q
        "error": None,
        "source": None,
    }

    # Step 1: Try Screener.in FIRST (best data for Indian stocks)
    try:
        symbol = ticker.replace(".NS", "").upper()
        screener_data = fetch_screener_data(symbol)
        
        if screener_data.get("pe") or screener_data.get("roce"):
            # We got good data from Screener.in
            result["pe"] = screener_data.get("pe")
            result["pb"] = screener_data.get("pb")
            result["roe"] = screener_data.get("roe")
            result["roce"] = screener_data.get("roce")
            result["debt_to_equity"] = screener_data.get("debt_equity")
            result["promoter_holding"] = screener_data.get("promoter_holding")
            result["pledged_pct"] = screener_data.get("pledged_pct")
            result["dividend_yield"] = screener_data.get("dividend_yield")
            result["profit_growth_3yr"] = screener_data.get("profit_growth_3y")
            result["sales_growth_3yr"] = screener_data.get("sales_growth_3y")
            result["source"] = "screener.in"
            logger.debug(f"  ✓ Got data from Screener.in for {ticker}")
    except Exception as e:
        logger.debug(f"  Screener.in failed for {ticker}: {e}")

    # Step 2: Fallback to yfinance for price/market cap if needed
    if not result.get("price") and yf:
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.info or {}

            # Price & market data
            price = info.get("currentPrice") or info.get("last_price")
            if price:
                result["price"] = float(price)
                result["source"] = result.get("source", "") + "+yfinance"

                # Market cap (convert to Crores)
                mkt_cap = info.get("marketCap")
                if mkt_cap:
                    result["market_cap_cr"] = float(mkt_cap) / 1_00_00_000

                # 52-week data
                low_52w = info.get("fiftyTwoWeekLow")
                high_52w = info.get("fiftyTwoWeekHigh")
                if low_52w:
                    result["price_52w_low"] = float(low_52w)
                if high_52w:
                    result["price_52w_high"] = float(high_52w)

                # % up from 52w low
                if result["price_52w_low"] and result["price"] > 0:
                    up_pct = ((result["price"] - result["price_52w_low"]) / result["price_52w_low"]) * 100
                    result["up_52w_pct"] = up_pct

                # Fill in missing PE/PB from yfinance if Screener.in didn't have it
                if not result.get("pe"):
                    pe = info.get("trailingPE")
                    if pe and pe > 0:
                        result["pe"] = float(pe)
                
                if not result.get("pb"):
                    pb = info.get("priceToBook")
                    if pb and pb > 0:
                        result["pb"] = float(pb)

                logger.debug(f"  ✓ Got price/market cap from yfinance for {ticker}")

        except Exception as e:
            logger.debug(f"  yfinance failed for {ticker}: {e}")

    if not result.get("price"):
        result["error"] = "Could not fetch price data"
        logger.warning(f"  ✗ No price data for {ticker}")

    return result


# ─────────────────────────────────────────────────────────────────
# Stock Screener
# ─────────────────────────────────────────────────────────────────

class StockScreener:
    """Screen stocks against multiple strategies."""

    def __init__(self):
        self.strategies: List[ScreeningStrategy] = []
        self._nse_universe: Optional[List[str]] = None

    def add_strategy(self, strategy: ScreeningStrategy) -> None:
        """Add a screening strategy."""
        self.strategies.append(strategy)
        logger.info(f"Added strategy: {strategy.name}")

    def add_strategies(self, strategy_names: List[str]) -> None:
        """Add strategies by name from ALL_STRATEGIES registry."""
        for name in strategy_names:
            if name not in ALL_STRATEGIES:
                logger.warning(f"Strategy '{name}' not found. Available: {list(ALL_STRATEGIES.keys())}")
                continue
            self.add_strategy(ALL_STRATEGIES[name])

    def load_nse_universe(self, universe_file: str = None) -> None:
        """Load NSE stock universe from file."""
        if universe_file and os.path.exists(universe_file):
            with open(universe_file) as f:
                self._nse_universe = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            logger.info(f"Loaded {len(self._nse_universe)} tickers from {universe_file}")
        else:
            self._nse_universe = []

    def search(
        self,
        tickers: Optional[List[str]] = None,
        universe_file: Optional[str] = None,
    ) -> Dict[str, List[Dict]]:
        """Search stocks against all strategies."""
        if not self.strategies:
            logger.warning("No strategies loaded. Add strategies first.")
            return {}

        if universe_file:
            self.load_nse_universe(universe_file)

        stocks_to_screen = tickers or self._nse_universe or []

        if not stocks_to_screen:
            logger.warning("No stocks to screen. Provide tickers or load universe.")
            return {}

        logger.info(f"Screening {len(stocks_to_screen)} stocks against {len(self.strategies)} strategies...")

        results: Dict[str, List[Dict]] = {s.name: [] for s in self.strategies}

        for idx, ticker in enumerate(stocks_to_screen, 1):
            logger.info(f"  [{idx}/{len(stocks_to_screen)}] Fetching {ticker}...")
            metrics = _fetch_stock_metrics(ticker)

            if metrics["error"]:
                logger.debug(f"    Skipped: {metrics['error']}")
                continue

            # Test against each strategy
            for strategy in self.strategies:
                passes, reasons = strategy.validate_stock(metrics)

                result_entry = {
                    "ticker": ticker,
                    "label": strategy.label if passes else "Does not meet criteria",
                    "passes": passes,
                    "metrics": metrics,
                    "reasons": reasons,
                }

                results[strategy.name].append(result_entry)

            # Small delay to avoid overwhelming Screener.in
            time.sleep(0.5)

        # Sort each strategy's results by strongest match
        for strategy_name in results:
            results[strategy_name].sort(
                key=lambda x: -sum(1 for r in x["reasons"] if r.startswith("✓"))
            )

        return results


# ─────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────

def format_screening_results(results: Dict[str, List[Dict]]) -> str:
    """Format screening results into a readable report."""
    if not results:
        return "No results."

    msg = f"NSE Stock Screener Results\n"
    msg += f"Date: {date.today().strftime('%d %b %Y')}\n"
    msg += "=" * 50 + "\n"

    for strategy_name, matches in results.items():
        passed = [m for m in matches if m["passes"]]
        failed = [m for m in matches if not m["passes"]]

        msg += f"\n{strategy_name.upper()}\n"
        msg += f"Total screened: {len(matches)} | Passed: {len(passed)}\n"
        msg += "-" * 50 + "\n"

        if passed:
            msg += "MATCHED:\n"
            for stock in passed[:10]:
                m = stock["metrics"]
                pe_str = f"{m['pe']:.2f}" if m['pe'] else "N/A"
                pb_str = f"{m['pb']:.2f}" if m['pb'] else "N/A"
                roce_str = f"{m['roce']:.1f}%" if m['roce'] else "N/A"
                mc_str = f"{m['market_cap_cr']:.0f}Cr" if m['market_cap_cr'] else "N/A"
                price_str = f"{m['price']:.2f}" if m['price'] else "N/A"
                msg += (
                    f"\n  {stock['ticker']} -> {stock['label']}\n"
                    f"    Price: {price_str} | Market Cap: {mc_str}\n"
                    f"    P/E: {pe_str} | P/B: {pb_str} | ROCE: {roce_str}\n"
                )
            if len(passed) > 10:
                msg += f"\n  ... and {len(passed) - 10} more\n"
        else:
            msg += "MATCHED: None\n"

        if failed and len(failed) <= 5:
            msg += "\nFAILED:\n"
            for stock in failed:
                failed_reasons = [r for r in stock["reasons"] if r.startswith("x")]
                msg += f"\n  {stock['ticker']}\n"
                for reason in failed_reasons[:2]:
                    msg += f"    {reason}\n"

    return msg
