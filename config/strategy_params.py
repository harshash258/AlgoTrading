"""
config/strategy_params.py — Strategy-specific hyperparameter defaults.
"""

# ─────────────────────────────────────────────
# STRATEGY DEFAULTS
# ─────────────────────────────────────────────

# Trend Following (MA Crossover)
TREND_FAST_MA              = 25  # fast moving average period
TREND_SLOW_MA              = 75  # slow moving average period
TREND_SIGNAL_LOOKBACK      = 5   # legacy, kept for reference

# Trend Following — enhanced filters
TREND_MA_DIRECTION         = 200 # higher timeframe MA for direction filter
TREND_ADX_PERIOD           = 14  # ADX calculation period
TREND_ADX_THRESHOLD        = 20.0# minimum ADX to allow entry (< 20 = choppy)
TREND_CONFIRM_BARS         = 1   # crossover must hold N bars before entry
TREND_TIME_STOP_DAYS       = 20  # exit if no profit within N days (0 = off)

# RSI Strategy
RSI_PERIOD                 = 14  # RSI calculation period
RSI_OVERSOLD               = 25  # buy CE when RSI crosses above this (was oversold)
RSI_OVERBOUGHT             = 65  # buy PE when RSI crosses below this (was overbought)
RSI_CONFIRM_BARS           = 1   # bars RSI must hold past threshold before entry
RSI_TIME_STOP_DAYS         = 10  # exit if not profitable within N days

# Mean Reversion (Short Strangle)
MR_IV_PERCENTILE_ENTRY     = 70  # enter short strangle when IV > 70th pct
MR_IV_PERCENTILE_EXIT      = 30  # exit when IV < 30th pct
MR_OTM_DELTA               = 0.20# target delta for OTM strikes

# Bollinger Band Mean Reversion Strategy
BB_PERIOD                  = 20  # SMA period for middle band
BB_STD_MULT                = 2.0 # ATR multiplier for band width
BB_ATR_PERIOD              = 14  # ATR calculation period
BB_USE_TREND_FILTER        = False # align with 200MA trend (False = take all reversions)
BB_VIX_MIN                 = 11.0# skip if VIX below this (options too cheap)
BB_VIX_MAX                 = 35.0# skip if VIX above this (panic/crash regime)
BB_CONFIRM_BARS            = 1   # bars price must stay outside band before entry
BB_TIME_STOP_DAYS          = 8   # exit if not profitable within N days

# Confluence Strategy (MA crossover + RSI agreement)
CONFLUENCE_RSI_LOOKBACK    = 30  # bars to look back for recent RSI cross (wider = suits slow MAs)
CONFLUENCE_RSI_ENTRY_MAX   = 65.0# RSI must be below this to enter CE (not overbought)
CONFLUENCE_RSI_ENTRY_MIN   = 35.0# RSI must be above this to enter PE (not oversold)
CONFLUENCE_USE_TREND_FILTER= False# align with 200MA trend
CONFLUENCE_TIME_STOP_DAYS  = 15  # exit if not profitable within N days

# ORB Strategy (Opening Range Breakout)
ORB_BREAKOUT_PCT           = 0.5 # close must be this % above/below open (daily proxy)
ORB_GAP_MAX_PCT            = 1.5 # skip if open is already gapped > this % from prev close
ORB_ADX_THRESHOLD          = 20.0# minimum ADX for entry (0 = disabled)
ORB_USE_ADX_FILTER         = False# enable ADX trend-strength filter
ORB_TIME_STOP_DAYS         = 5   # exit if not profitable within N days

# Long Straddle / Strangle (Long Volatility)
STRADDLE_IV_ENTRY_PCT      = 30  # enter when IV percentile is BELOW this (cheap vol)
STRADDLE_IV_EXIT_PCT       = 60  # exit when IV percentile rises ABOVE this (vol expanded)
STRADDLE_OTM_DELTA         = 0.0 # 0 = ATM straddle; 0.25 = strangle OTM legs
STRADDLE_TIME_STOP_DAYS    = 10  # exit after N days (theta burn management)
STRADDLE_VIX_MIN           = 10.0# don't buy vol when market is truly dead
STRADDLE_VIX_MAX           = 30.0# don't buy vol in extreme panic (already expanded)

# VWAP Reversion / Breakout
VWAP_WINDOW                = 20  # rolling bars for VWAP calculation
VWAP_STD_MULT              = 1.5 # standard deviation multiplier for bands
VWAP_MODE                  = "reversion" # "reversion" or "breakout"
VWAP_TIME_STOP_DAYS        = 8   # exit if not profitable within N days

# Gap Fade Strategy
GAP_MIN_PCT                = 0.5 # minimum gap % to consider fading
GAP_MAX_PCT                = 2.0 # maximum gap % — above this is genuine news
GAP_REQUIRE_NO_FOLLOW      = True# require partial gap fill by close
GAP_TREND_FILTER           = False# only fade gaps against the 200MA trend
GAP_TIME_STOP_DAYS         = 4   # gaps fill quickly or fail — short stop

# Iron Condor (Delta-Neutral)
IC_IV_ENTRY_PCT            = 60  # enter when IV percentile > this (sell expensive vol)
IC_IV_EXIT_PCT             = 35  # exit when IV percentile drops below this
IC_BODY_DELTA              = 0.25# delta for short body strikes
IC_WING_DELTA              = 0.10# delta for long wing strikes (risk cap)
IC_ADX_CHOPPY_THRESHOLD    = 22.0# only enter when ADX < this (market is choppy)
IC_USE_ADX_FILTER          = True# enforce chop filter (key differentiator)
IC_VIX_MIN                 = 13.0# floor: need enough premium to make legs worthwhile
IC_VIX_MAX                 = 40.0# ceiling: too volatile — wings get too expensive
IC_TIME_STOP_DAYS          = 15  # exit before expiry if held too long
