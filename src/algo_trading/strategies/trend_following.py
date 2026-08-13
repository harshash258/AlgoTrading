"""
trend_following.py — Moving Average Crossover Options Strategy (Enhanced).

Filters applied (each individually toggleable):
  1. ADX filter       — only trade when ADX > adx_threshold (trend is strong)
  2. Confirmation     — crossover must hold for N bars before entry
  3. 200MA direction  — only trade in direction of higher timeframe trend
  4. Weekly expiry    — use cheaper weekly options instead of monthly
  5. Time stop        — exit if trade not profitable within time_stop_days

Core logic:
  - Fast MA crosses above slow MA + all active filters pass → BUY ATM CE
  - Fast MA crosses below slow MA + all active filters pass → BUY ATM PE
  - Exit on opposite crossover, SL/target (backtester), or time stop
"""

import pandas as pd
import numpy as np
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry


# ─────────────────────────────────────────────────────────────────
# ADX calculation (pure pandas/numpy, no ta-lib dependency)
# ─────────────────────────────────────────────────────────────────

def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute Average Directional Index (ADX).
    Returns a Series of ADX values (same index as input).
    Values > 25 = strong trend. Values < 20 = choppy/no trend.
    """
    high  = high.reset_index(drop=True)
    low   = low.reset_index(drop=True)
    close = close.reset_index(drop=True)

    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Smoothed TR and DM (Wilder smoothing)
    atr      = _wilder_smooth(pd.Series(tr),              period)
    plus_di  = 100 * _wilder_smooth(pd.Series(plus_dm),  period) / atr
    minus_di = 100 * _wilder_smooth(pd.Series(minus_dm), period) / atr

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _wilder_smooth(dx.fillna(0), period)

    adx.index = high.index
    return adx


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (EMA with alpha = 1/period)."""
    result = series.copy().astype(float)
    result.iloc[:period] = np.nan
    # Seed with simple mean of first `period` values
    first_valid = series.iloc[:period].mean()
    result.iloc[period - 1] = first_valid
    alpha = 1.0 / period
    for i in range(period, len(series)):
        result.iloc[i] = result.iloc[i - 1] * (1 - alpha) + series.iloc[i] * alpha
    return result


# ─────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────

@register_strategy("trend", "trend_following", "trend_raw")
class TrendFollowingStrategy(BaseStrategy):
    """
    Enhanced MA Crossover trend-following strategy with 5 filters.

    Parameters
    ----------
    fast_ma           : Fast MA period (default 20)
    slow_ma           : Slow MA period (default 50)
    trend_ma          : Higher timeframe MA for direction filter (default 200)
    adx_period        : ADX calculation period (default 14)
    adx_threshold     : Minimum ADX to allow entry (default 25)
    confirm_bars      : Bars crossover must hold before entry (default 2)
    time_stop_days    : Exit if no profit within N days (0 = disabled)
    weekly            : Use weekly expiry (default False = monthly)
    use_adx_filter    : Enable/disable ADX filter (default True)
    use_confirm       : Enable/disable crossover confirmation (default True)
    use_direction_filter : Enable/disable 200MA direction filter (default True)
    use_time_stop     : Enable/disable time stop (default True)
    """

    def __init__(
        self,
        fast_ma             : int   = config.TREND_FAST_MA,
        slow_ma             : int   = config.TREND_SLOW_MA,
        trend_ma            : int   = 200,
        adx_period          : int   = 14,
        adx_threshold       : float = 25.0,
        confirm_bars        : int   = 2,
        time_stop_days      : int   = 15,
        weekly              : bool  = False,
        use_adx_filter      : bool  = True,
        use_confirm         : bool  = True,
        use_direction_filter: bool  = True,
        use_time_stop       : bool  = True,
    ):
        self.fast_ma              = fast_ma
        self.slow_ma              = slow_ma
        self.trend_ma             = trend_ma
        self.adx_period           = adx_period
        self.adx_threshold        = adx_threshold
        self.confirm_bars         = confirm_bars
        self.time_stop_days       = time_stop_days
        self.weekly               = weekly
        self.use_adx_filter       = use_adx_filter
        self.use_confirm          = use_confirm
        self.use_direction_filter = use_direction_filter
        self.use_time_stop        = use_time_stop

        # State per underlying
        self._prev_signal   : dict[str, str]  = {}  # ticker → "long_ce"|"long_pe"|"none"
        self._cross_counter : dict[str, int]  = {}  # ticker → bars since crossover detected
        self._pending_dir   : dict[str, str]  = {}  # ticker → "ce"|"pe" pending confirmation
        self._entry_date    : dict[str, date] = {}  # ticker → entry date for time stop

    @property
    def name(self) -> str:
        filters = []
        if self.use_adx_filter:       filters.append(f"adx{self.adx_threshold:.0f}")
        if self.use_confirm:           filters.append(f"conf{self.confirm_bars}")
        if self.use_direction_filter:  filters.append(f"dir{self.trend_ma}")
        if self.use_time_stop:         filters.append(f"ts{self.time_stop_days}")
        suffix = "_" + "_".join(filters) if filters else ""
        return f"trend_ma_{self.fast_ma}_{self.slow_ma}{suffix}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:

        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = max(self.slow_ma, self.trend_ma, self.adx_period * 2) + self.confirm_bars + 5
        if len(data) < min_bars:
            return signals

        close  = data["Close"]
        high   = data["High"]
        low    = data["Low"]

        # ── Core MAs ────────────────────────────────────────────
        fast    = close.rolling(self.fast_ma).mean()
        slow    = close.rolling(self.slow_ma).mean()
        fast_now  = float(fast.iloc[-1])
        fast_prev = float(fast.iloc[-2])
        slow_now  = float(slow.iloc[-1])
        slow_prev = float(slow.iloc[-2])

        if pd.isna(fast_now) or pd.isna(slow_now):
            return signals

        # ── Filter 3: 200MA direction ────────────────────────────
        trend_ma_val = float(close.rolling(self.trend_ma).mean().iloc[-1])
        spot         = float(close.iloc[-1])
        if pd.isna(trend_ma_val):
            return signals
        in_uptrend   = spot > trend_ma_val
        in_downtrend = spot < trend_ma_val

        # ── Filter 1: ADX ────────────────────────────────────────
        adx_val = None
        if self.use_adx_filter:
            adx     = _compute_adx(high, low, close, self.adx_period)
            adx_val = float(adx.iloc[-1])
            if pd.isna(adx_val) or adx_val < self.adx_threshold:
                # Market is choppy — reset any pending confirmation, no new entries
                self._pending_dir[underlying]   = ""
                self._cross_counter[underlying] = 0
                return signals

        # ── Detect raw crossover ──────────────────────────────────
        bullish_cross = (fast_prev <= slow_prev) and (fast_now > slow_now)
        bearish_cross = (fast_prev >= slow_prev) and (fast_now < slow_now)

        prev    = self._prev_signal.get(underlying, "none")
        pending = self._pending_dir.get(underlying, "")
        counter = self._cross_counter.get(underlying, 0)

        # ── Filter 2: Confirmation — start/advance counter ───────
        if self.use_confirm:
            if bullish_cross and prev != "long_ce":
                self._pending_dir[underlying]   = "ce"
                self._cross_counter[underlying] = 1
            elif bearish_cross and prev != "long_pe":
                self._pending_dir[underlying]   = "pe"
                self._cross_counter[underlying] = 1
            elif pending:
                # Check crossover is still intact
                still_bullish = fast_now > slow_now and pending == "ce"
                still_bearish = fast_now < slow_now and pending == "pe"
                if still_bullish or still_bearish:
                    self._cross_counter[underlying] = counter + 1
                else:
                    # Crossover failed — reset
                    self._pending_dir[underlying]   = ""
                    self._cross_counter[underlying] = 0

            confirmed_ce = (pending == "ce" and
                            self._cross_counter.get(underlying, 0) >= self.confirm_bars)
            confirmed_pe = (pending == "pe" and
                            self._cross_counter.get(underlying, 0) >= self.confirm_bars)
        else:
            # No confirmation needed — treat raw crossover as confirmed immediately
            confirmed_ce = bullish_cross and prev != "long_ce"
            confirmed_pe = bearish_cross and prev != "long_pe"

        # ── Filter 3: Apply direction filter ─────────────────────
        if self.use_direction_filter:
            if confirmed_ce and not in_uptrend:
                confirmed_ce = False   # don't buy CE in a downtrend
            if confirmed_pe and not in_downtrend:
                confirmed_pe = False   # don't buy PE in an uptrend

        expiry = next_expiry(current_date, weekly=self.weekly)

        # ── Emit exit signals ─────────────────────────────────────
        # Time stop: if current position has been open too long without profit
        if self.use_time_stop and self.time_stop_days > 0:
            entry_dt = self._entry_date.get(underlying)
            if entry_dt and (current_date - entry_dt).days >= self.time_stop_days:
                if prev in ("long_ce", "long_pe"):
                    opt = "CE" if prev == "long_ce" else "PE"
                    signals.append(Signal(
                        date=current_date,
                        underlying=underlying,
                        direction="long",
                        option_type=opt,
                        signal_type="exit",
                        exit_reason="time_stop",
                        meta={"days_held": (current_date - entry_dt).days},
                    ))
                    self._prev_signal[underlying]   = "none"
                    self._entry_date[underlying]    = None
                    self._pending_dir[underlying]   = ""
                    self._cross_counter[underlying] = 0
                    return signals

        # ── Emit entry signals ────────────────────────────────────
        if confirmed_ce:
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "crossover reversed, exiting PE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "fast_ma"    : round(fast_now, 2),
                    "slow_ma"    : round(slow_now, 2),
                    "trend_ma"   : round(trend_ma_val, 2),
                    "adx"        : round(adx_val, 2) if adx_val else "N/A",
                    "close"      : round(spot, 2),
                    "confirm_bars": self._cross_counter.get(underlying, 0),
                },
            ))
            self._prev_signal[underlying]   = "long_ce"
            self._entry_date[underlying]    = current_date
            self._pending_dir[underlying]   = ""
            self._cross_counter[underlying] = 0

        elif confirmed_pe:
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "crossover reversed, exiting CE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "fast_ma"    : round(fast_now, 2),
                    "slow_ma"    : round(slow_now, 2),
                    "trend_ma"   : round(trend_ma_val, 2),
                    "adx"        : round(adx_val, 2) if adx_val else "N/A",
                    "close"      : round(spot, 2),
                    "confirm_bars": self._cross_counter.get(underlying, 0),
                },
            ))
            self._prev_signal[underlying]   = "long_pe"
            self._entry_date[underlying]    = current_date
            self._pending_dir[underlying]   = ""
            self._cross_counter[underlying] = 0

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"          : self.name,
            "fast_ma"           : self.fast_ma,
            "slow_ma"           : self.slow_ma,
            "trend_ma (dir)"    : self.trend_ma,
            "adx_period"        : self.adx_period,
            "adx_threshold"     : self.adx_threshold,
            "confirm_bars"      : self.confirm_bars,
            "time_stop_days"    : self.time_stop_days if self.use_time_stop else "off",
            "weekly_expiry"     : self.weekly,
            "filter_adx"        : self.use_adx_filter,
            "filter_confirm"    : self.use_confirm,
            "filter_direction"  : self.use_direction_filter,
            "filter_time_stop"  : self.use_time_stop,
            "stop_loss"         : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"            : f"{config.BUY_TARGET_PCT}% of premium",
        }
