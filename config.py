"""
config.py — Central configuration for the Algo Trading system.
All parameters are defined here. Never hardcode values in other modules.
"""

from datetime import date

# ─────────────────────────────────────────────
# BACKTEST PERIOD
# ─────────────────────────────────────────────
BACKTEST_START = date(2015, 1, 1)
BACKTEST_END   = date.today()       # always runs up to today

# ─────────────────────────────────────────────
# CAPITAL & POSITION SIZING
# ─────────────────────────────────────────────
STARTING_CAPITAL      = 500_000        # ₹5,00,000
RISK_PER_TRADE_PCT    = 2.0            # % of capital risked per trade
MAX_OPEN_POSITIONS    = 5              # max concurrent open trades
MAX_LOTS_PER_TRADE    = 10             # hard cap on lots per single trade
MAX_DRAWDOWN_HALT_PCT = 15.0           # halt new trades if drawdown > this %

# ─────────────────────────────────────────────
# OPTIONS PARAMETERS
# ─────────────────────────────────────────────
RISK_FREE_RATE        = 0.065          # 6.5% annualised (91-day T-bill proxy)
SLIPPAGE_PCT          = 1.5            # % of premium slippage on entry & exit

# Default stop loss / target per trade side
BUY_STOP_LOSS_PCT     = 40.0  # exit buy if premium falls 50%
BUY_TARGET_PCT        = 200.0  # exit buy if premium doubles
SELL_STOP_LOSS_PCT    = 100.0          # exit sell if premium hits 2x received
SELL_TARGET_PCT       = 50.0           # exit sell if premium decays 50%

# Close all positions N days before expiry
DAYS_BEFORE_EXPIRY_EXIT = 1

# ─────────────────────────────────────────────
# LOT SIZES (NSE current lot sizes)
# ─────────────────────────────────────────────
LOT_SIZES = {
    "^NSEI":       25,    # Nifty 50
    "^NSEBANK":    15,    # Bank Nifty
    "NIFTY_FIN_SERVICE.NS": 40,    # Fin Nifty
    "NIFTY":       25,
    "BANKNIFTY":   15,
    "FINNIFTY":    40,
    "MIDCPNIFTY":  75,
    # Add individual stock lot sizes as needed
    "RELIANCE.NS": 250,
    "TCS.NS":      150,
    "INFY.NS":     300,
    "HDFCBANK.NS": 550,
    "ICICIBANK.NS":700,
    "SBIN.NS":     1500,
}
DEFAULT_LOT_SIZE = 100   # fallback if symbol not in dict

# ─────────────────────────────────────────────
# BROKERAGE & TAX (Groww flat fee model)
# ─────────────────────────────────────────────
BROKERAGE_PER_ORDER   = 20.0           # ₹20 flat per order
BROKERAGE_MAX_PCT     = 0.05           # or 0.05% of order value, whichever lower
STT_BUY_PCT           = 0.0           # STT on options buy side = 0
STT_SELL_PCT          = 0.125          # 0.125% on sell side (options)
EXCHANGE_CHARGE_PCT   = 0.053          # NSE transaction charge %
GST_PCT               = 18.0           # 18% on brokerage + exchange charges
SEBI_CHARGE_PER_CR    = 10.0           # ₹10 per crore turnover
STAMP_DUTY_BUY_PCT    = 0.003          # 0.003% on buy side

# ─────────────────────────────────────────────
# DATA SOURCES
# ─────────────────────────────────────────────
INDIA_VIX_TICKER      = "^INDIAVIX"    # Yahoo Finance ticker for India VIX
DATA_CACHE_DIR        = "data/cache"
RAW_DATA_DIR          = "data/raw"
OPTIONS_DATA_DIR      = "data/options"

# Optional: path to NSE bhavcopy ZIP folder for real options chain data
# Download from: https://www.nseindia.com/market-data/historical-derivatives-data
# Leave as None to use synthetic Black-Scholes pricing (default)
BHAVCOPY_FOLDER       = "data/bhavcopy"  # activated — real NSE option chain data
BHAVCOPY_SYMBOL       = "NIFTY"          # NSE symbol in bhavcopy files

# Target underlyings for backtesting
# Yahoo Finance tickers
UNDERLYINGS = [
    "^NSEI",        # Nifty 50
    "^NSEBANK",     # Bank Nifty
    "NIFTY_FIN_SERVICE.NS",  # Fin Nifty (NSE:FINNIFTY)
]

# Strike step per underlying (for ATM rounding)
STRIKE_STEPS = {
    "^NSEI":       50.0,
    "^NSEBANK":   100.0,
    "NIFTY_FIN_SERVICE.NS": 50.0,
}

# ─────────────────────────────────────────────
# STRATEGY DEFAULTS
# ─────────────────────────────────────────────
# Trend Following (MA Crossover)
TREND_FAST_MA              = 25  # fast moving average period
TREND_SLOW_MA              = 75  # slow moving average period
TREND_SIGNAL_LOOKBACK      = 5       # legacy, kept for reference

# Trend Following — enhanced filters
TREND_MA_DIRECTION         = 200     # higher timeframe MA for direction filter
TREND_ADX_PERIOD           = 14      # ADX calculation period
TREND_ADX_THRESHOLD        = 20.0    # minimum ADX to allow entry (< 20 = choppy)
TREND_CONFIRM_BARS         = 1       # crossover must hold N bars before entry
TREND_TIME_STOP_DAYS       = 20      # exit if no profit within N days (0 = off)

# RSI Strategy
RSI_PERIOD             = 14      # RSI calculation period
RSI_OVERSOLD           = 25  # buy CE when RSI crosses above this (was oversold)
RSI_OVERBOUGHT         = 65  # buy PE when RSI crosses below this (was overbought)
RSI_CONFIRM_BARS       = 1       # bars RSI must hold past threshold before entry
RSI_TIME_STOP_DAYS     = 10      # exit if not profitable within N days

# Mean Reversion (Short Strangle)
MR_IV_PERCENTILE_ENTRY = 70      # enter short strangle when IV > 70th pct
MR_IV_PERCENTILE_EXIT  = 30      # exit when IV < 30th pct
MR_OTM_DELTA           = 0.20    # target delta for OTM strikes

# ─────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────
REPORTS_DIR           = "reports"
SIGNALS_DIR           = "signals"
TEMPLATES_DIR         = "templates"
REPORT_ROWS_PER_PAGE  = 50
