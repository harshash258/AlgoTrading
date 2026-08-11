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

# Bollinger Band Mean Reversion Strategy
BB_PERIOD              = 20      # SMA period for middle band
BB_STD_MULT            = 2.0     # ATR multiplier for band width
BB_ATR_PERIOD          = 14      # ATR calculation period
BB_USE_TREND_FILTER    = False   # align with 200MA trend (False = take all reversions)
BB_VIX_MIN             = 11.0   # skip if VIX below this (options too cheap)
BB_VIX_MAX             = 35.0   # skip if VIX above this (panic/crash regime)
BB_CONFIRM_BARS        = 1       # bars price must stay outside band before entry
BB_TIME_STOP_DAYS      = 8       # exit if not profitable within N days

# Confluence Strategy (MA crossover + RSI agreement)
CONFLUENCE_RSI_LOOKBACK      = 30    # bars to look back for recent RSI cross (wider = suits slow MAs)
CONFLUENCE_RSI_ENTRY_MAX     = 65.0  # RSI must be below this to enter CE (not overbought)
CONFLUENCE_RSI_ENTRY_MIN     = 35.0  # RSI must be above this to enter PE (not oversold)
CONFLUENCE_USE_TREND_FILTER  = False # align with 200MA trend
CONFLUENCE_TIME_STOP_DAYS    = 15    # exit if not profitable within N days

# ─────────────────────────────────────────────
# ORB Strategy (Opening Range Breakout)
# ─────────────────────────────────────────────
ORB_BREAKOUT_PCT    = 0.5    # close must be this % above/below open (daily proxy)
ORB_GAP_MAX_PCT     = 1.5    # skip if open is already gapped > this % from prev close
ORB_ADX_THRESHOLD   = 20.0   # minimum ADX for entry (0 = disabled)
ORB_USE_ADX_FILTER  = False  # enable ADX trend-strength filter
ORB_TIME_STOP_DAYS  = 5      # exit if not profitable within N days

# ─────────────────────────────────────────────
# Long Straddle / Strangle (Long Volatility)
# ─────────────────────────────────────────────
STRADDLE_IV_ENTRY_PCT   = 30     # enter when IV percentile is BELOW this (cheap vol)
STRADDLE_IV_EXIT_PCT    = 60     # exit when IV percentile rises ABOVE this (vol expanded)
STRADDLE_OTM_DELTA      = 0.0    # 0 = ATM straddle; 0.25 = strangle OTM legs
STRADDLE_TIME_STOP_DAYS = 10     # exit after N days (theta burn management)
STRADDLE_VIX_MIN        = 10.0   # don't buy vol when market is truly dead
STRADDLE_VIX_MAX        = 30.0   # don't buy vol in extreme panic (already expanded)

# ─────────────────────────────────────────────
# VWAP Reversion / Breakout
# ─────────────────────────────────────────────
VWAP_WINDOW         = 20     # rolling bars for VWAP calculation
VWAP_STD_MULT       = 1.5    # standard deviation multiplier for bands
VWAP_MODE           = "reversion"  # "reversion" or "breakout"
VWAP_TIME_STOP_DAYS = 8      # exit if not profitable within N days

# ─────────────────────────────────────────────
# Gap Fade Strategy
# ─────────────────────────────────────────────
GAP_MIN_PCT             = 0.5    # minimum gap % to consider fading
GAP_MAX_PCT             = 2.0    # maximum gap % — above this is genuine news
GAP_REQUIRE_NO_FOLLOW   = True   # require partial gap fill by close
GAP_TREND_FILTER        = False  # only fade gaps against the 200MA trend
GAP_TIME_STOP_DAYS      = 4      # gaps fill quickly or fail — short stop

# ─────────────────────────────────────────────
# Iron Condor (Delta-Neutral)
# ─────────────────────────────────────────────
IC_IV_ENTRY_PCT          = 60     # enter when IV percentile > this (sell expensive vol)
IC_IV_EXIT_PCT           = 35     # exit when IV percentile drops below this
IC_BODY_DELTA            = 0.25   # delta for short body strikes
IC_WING_DELTA            = 0.10   # delta for long wing strikes (risk cap)
IC_ADX_CHOPPY_THRESHOLD  = 22.0   # only enter when ADX < this (market is choppy)
IC_USE_ADX_FILTER        = True   # enforce chop filter (key differentiator)
IC_VIX_MIN               = 13.0   # floor: need enough premium to make legs worthwhile
IC_VIX_MAX               = 40.0   # ceiling: too volatile — wings get too expensive
IC_TIME_STOP_DAYS        = 15     # exit before expiry if held too long

# ─────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────
REPORTS_DIR           = "reports"
SIGNALS_DIR           = "signals"
TEMPLATES_DIR         = "templates"
REPORT_ROWS_PER_PAGE  = 50
