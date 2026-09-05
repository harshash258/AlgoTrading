"""
config/settings.py — Core system and portfolio settings.
"""

from datetime import date
import os

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
BUY_STOP_LOSS_PCT     = 40.0           # exit buy if premium falls 40%
BUY_TARGET_PCT        = 200.0          # exit buy if premium doubles
SELL_STOP_LOSS_PCT    = 100.0          # exit sell if premium hits 2x received
SELL_TARGET_PCT       = 50.0           # exit sell if premium decays 50%

# Close all positions N days before expiry
DAYS_BEFORE_EXPIRY_EXIT = 1

# ─────────────────────────────────────────────
# LOT SIZES (NSE current lot sizes)
# ─────────────────────────────────────────────
LOT_SIZES = {
    "^NSEI":       65,    # Nifty 50
    "^NSEBANK":    30,    # Bank Nifty
    "NIFTY_FIN_SERVICE.NS": 60,    # Fin Nifty
    "NIFTY":       65,
    "BANKNIFTY":   30,
    "FINNIFTY":    60,
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
STT_BUY_PCT           = 0.0            # STT on options buy side = 0
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

BHAVCOPY_FOLDER       = "data/bhavcopy"  # activated — real NSE option chain data
BHAVCOPY_SYMBOL       = "NIFTY"          # legacy default NSE symbol in bhavcopy files
BHAVCOPY_SYMBOLS      = {
    "^NSEI": "NIFTY",
    "^NSEBANK": "BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "FINNIFTY",
}
EXECUTION_TIMING      = "next_open"      # next_open | same_day_close

VIX_STALE_DAYS        = 3
ALLOW_FALLBACK_VIX_BACKTEST = True

MAX_STRIKE_DISTANCE = {
    "^NSEI": 100.0,
    "^NSEBANK": 200.0,
    "NIFTY_FIN_SERVICE.NS": 100.0,
}
MIN_OPTION_VOLUME = {
    "^NSEI": 100,
    "^NSEBANK": 100,
    "NIFTY_FIN_SERVICE.NS": 50,
}
MIN_OPTION_OI = {
    "^NSEI": 1000,
    "^NSEBANK": 1000,
    "NIFTY_FIN_SERVICE.NS": 500,
}

# Target underlyings for backtesting
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

MIN_REGIME_TRADES_FOR_RECOMMENDATION = 5

# ─────────────────────────────────────────────
# REPORTING & PATHS
# ─────────────────────────────────────────────
REPORTS_DIR           = "reports"
SIGNALS_DIR           = "signals"
TEMPLATES_DIR         = "templates"
REPORT_ROWS_PER_PAGE  = 50

# ─────────────────────────────────────────────
# WEEKLY REVIEW EMAIL
# ─────────────────────────────────────────────
WEEKLY_REVIEW_DIR     = "reports/weekly"
WEEKLY_REVIEW_RECIPIENTS = []  # fallback when WEEKLY_EMAIL_TO is not set
SMTP_TLS              = True
