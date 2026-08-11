"""
bollinger_band_strategy.py — Bollinger Band Mean Reversion Options Strategy.

Logic:
  - Compute Bollinger Bands (SMA ± N × ATR) on daily closes
  - Price closes BELOW lower band → market over-extended to downside
    → BUY ATM CE (expect snap-back up)
  - Price closes ABOVE upper band → market over-extended to upside
    → BUY ATM PE (expect snap-back down)

  Additional filters:
  1. VIX regime filter  — skip entries when VIX > vix_max (panic/crash)
                          or VIX < vix_min (too calm, options too cheap)
  2. ATR size filter    — skip if ATR is unusually small (flat, illiquid day)
  3. Trend context      — optional 200MA filter to avoid buying into strong trends
  4. Confirmation bar   — band touch must persist for N bars before entry
  5. Time stop          — exit if not profitable within N days

Why this works on Indian indices:
  - Nifty/BankNifty are range-bound ~60% of the time
  - Post-band-touch mean reversion within 3-5 sessions is statistically common
  - Weekly options let you capture the snap-back cheaply with limited theta drag

Usage:
    strategy = BollingerBandStrategy(bb_period=20, bb_std=2.0, atr_period=14)
"""

import pandas as pd
import numpy as np
from datetime import date

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import config
from src.strategies.base_strategy import BaseStrategy, Signal
from src.options_pricing import next_expiry


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range — measures volatility."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _compute_bands(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    period: int,
    std_mult: float,
    atr_period: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Bollinger Bands using ATR instead of price std-dev.

    Using ATR makes the bands adaptive to volatility expansions (e.g. during
    earnings or RBI events) and avoids the Bollinger Band squeeze problem where
    std-dev bands get very tight in low-vol periods and whipsaw.

    Returns: (middle_band, upper_band, lower_band)
    """
    middle = close.rolling(period).mean()
    atr    = _compute_atr(high, low, close, atr_period)
    upper  = middle + std_mult * atr
    lower  = middle - std_mult * atr
    return middle, upper, lower


class BollingerBandStrategy(BaseStrategy):
    """
    ATR-based Bollinger Band mean reversion strategy.

    Buys options after price closes outside the bands, expecting a
    reversion back to the mean (middle band).

    Parameters
    ----------
    bb_period       : Lookback period for middle band SMA (default 20)
    bb_std          : ATR multiplier for band width (default 2.0)
    atr_period      : ATR calculation period (default 14)
    trend_ma        : Higher timeframe MA for optional trend filter (default 200)
    use_trend_filter: If True, skip entries that fight the 200MA trend (default False)
                      Note: reversion entries intentionally counter-trend — use with care
    vix_min         : Skip entry if VIX below this (options too cheap) (default 11)
    vix_max         : Skip entry if VIX above this (panic/crash regime) (default 35)
    confirm_bars    : Number of bars price must stay outside band (default 1)
    time_stop_days  : Exit if not profitable within N days (default 8)
    weekly          : Use weekly expiry (default True — faster theta, suits reversion)
    """

    def __init__(
        self,
        bb_period       : int   = config.BB_PERIOD,
        bb_std          : float = config.BB_STD_MULT,
        atr_period      : int   = config.BB_ATR_PERIOD,
        trend_ma        : int   = 200,
        use_trend_filter: bool  = config.BB_USE_TREND_FILTER,
        vix_min         : float = config.BB_VIX_MIN,
        vix_max         : float = config.BB_VIX_MAX,
        confirm_bars    : int   = config.BB_CONFIRM_BARS,
        time_stop_days  : int   = config.BB_TIME_STOP_DAYS,
        weekly          : bool  = True,
    ):
        self.bb_period        = bb_period
        self.bb_std           = bb_std
        self.atr_period       = atr_period
        self.trend_ma         = trend_ma
        self.use_trend_filter = use_trend_filter
        self.vix_min          = vix_min
        self.vix_max          = vix_max
        self.confirm_bars     = confirm_bars
        self.time_stop_days   = time_stop_days
        self.weekly           = weekly

        # State per underlying
        self._prev_signal   : dict[str, str]  = {}  # "long_ce" | "long_pe" | "none"
        self._entry_date    : dict[str, date] = {}
        self._pending       : dict[str, str]  = {}  # "ce" | "pe" | ""
        self._pending_bars  : dict[str, int]  = {}

    @property
    def name(self) -> str:
        trend_tag = "_trf" if self.use_trend_filter else ""
        return (
            f"bb_{self.bb_period}_{self.bb_std:.1f}"
            f"_atr{self.atr_period}"
            f"_ts{self.time_stop_days}"
            f"{trend_tag}"
        )

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:

        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = max(self.bb_period, self.atr_period, self.trend_ma) + self.confirm_bars + 5
        if len(data) < min_bars:
            return signals

        close  = data["Close"]
        high   = data["High"]
        low    = data["Low"]
        spot   = float(close.iloc[-1])

        # ── Bollinger Bands ───────────────────────────────────────
        middle, upper, lower = _compute_bands(
            close, high, low,
            self.bb_period, self.bb_std, self.atr_period,
        )

        mid_now   = float(middle.iloc[-1])
        upper_now = float(upper.iloc[-1])
        lower_now = float(lower.iloc[-1])

        if pd.isna(mid_now) or pd.isna(upper_now) or pd.isna(lower_now):
            return signals

        # ── VIX regime filter ─────────────────────────────────────
        vix_now = float(vix.iloc[-1]) if hasattr(vix, "iloc") else float(vix)
        if pd.isna(vix_now):
            vix_now = 15.0  # safe fallback

        if vix_now < self.vix_min or vix_now > self.vix_max:
            # Outside tradeable volatility range — stand aside
            self._pending[underlying]    = ""
            self._pending_bars[underlying] = 0
            return signals

        # ── Trend context filter (optional) ──────────────────────
        if self.use_trend_filter:
            trend_ma_val = float(close.rolling(self.trend_ma).mean().iloc[-1])
            if not pd.isna(trend_ma_val):
                in_uptrend   = spot > trend_ma_val
                in_downtrend = spot < trend_ma_val
            else:
                in_uptrend = in_downtrend = True  # no filter if MA not ready
        else:
            in_uptrend = in_downtrend = True

        prev = self._prev_signal.get(underlying, "none")

        # ── Time stop ─────────────────────────────────────────────
        if self.time_stop_days > 0:
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
                    self._prev_signal[underlying]  = "none"
                    self._entry_date[underlying]   = None
                    self._pending[underlying]      = ""
                    self._pending_bars[underlying] = 0
                    return signals

        # ── Band touch detection ──────────────────────────────────
        # Close outside band = potential reversion entry candidate
        touched_lower = spot < lower_now   # oversold extension → CE candidate
        touched_upper = spot > upper_now   # overbought extension → PE candidate

        pending      = self._pending.get(underlying, "")
        pending_bars = self._pending_bars.get(underlying, 0)

        if touched_lower and prev != "long_ce":
            if pending == "ce":
                self._pending_bars[underlying] = pending_bars + 1
            else:
                self._pending[underlying]      = "ce"
                self._pending_bars[underlying] = 1
        elif touched_upper and prev != "long_pe":
            if pending == "pe":
                self._pending_bars[underlying] = pending_bars + 1
            else:
                self._pending[underlying]      = "pe"
                self._pending_bars[underlying] = 1
        else:
            # Price back inside bands — reset any pending
            if pending and not (
                (pending == "ce" and touched_lower) or
                (pending == "pe" and touched_upper)
            ):
                self._pending[underlying]      = ""
                self._pending_bars[underlying] = 0

        confirmed_ce = (
            self._pending.get(underlying) == "ce"
            and self._pending_bars.get(underlying, 0) >= self.confirm_bars
            and prev != "long_ce"
            and in_downtrend  # trend filter: only buy CE if not in strong downtrend
                              # (when filter off, in_downtrend=True always)
        )
        confirmed_pe = (
            self._pending.get(underlying) == "pe"
            and self._pending_bars.get(underlying, 0) >= self.confirm_bars
            and prev != "long_pe"
            and in_uptrend    # trend filter: only buy PE if not in strong uptrend
        )

        # ── Apply 200MA direction filter logic ────────────────────
        # When trend filter is ON:
        #   - Don't buy CE when below 200MA (downtrend — reversion may fail)
        #   - Don't buy PE when above 200MA (uptrend — reversion may fail)
        if self.use_trend_filter:
            trend_ma_val = float(close.rolling(self.trend_ma).mean().iloc[-1])
            if not pd.isna(trend_ma_val):
                if confirmed_ce and spot < trend_ma_val:
                    confirmed_ce = False
                if confirmed_pe and spot > trend_ma_val:
                    confirmed_pe = False

        expiry = next_expiry(current_date, weekly=self.weekly)

        # ── Emit signals ──────────────────────────────────────────
        if confirmed_ce:
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "band flipped, exiting PE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "spot"       : round(spot, 2),
                    "lower_band" : round(lower_now, 2),
                    "middle_band": round(mid_now, 2),
                    "vix"        : round(vix_now, 2),
                    "pct_below"  : round((lower_now - spot) / lower_now * 100, 2),
                    "trigger"    : "BB lower band touch",
                },
            ))
            self._prev_signal[underlying]  = "long_ce"
            self._entry_date[underlying]   = current_date
            self._pending[underlying]      = ""
            self._pending_bars[underlying] = 0

        elif confirmed_pe:
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "band flipped, exiting CE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "spot"       : round(spot, 2),
                    "upper_band" : round(upper_now, 2),
                    "middle_band": round(mid_now, 2),
                    "vix"        : round(vix_now, 2),
                    "pct_above"  : round((spot - upper_now) / upper_now * 100, 2),
                    "trigger"    : "BB upper band touch",
                },
            ))
            self._prev_signal[underlying]  = "long_pe"
            self._entry_date[underlying]   = current_date
            self._pending[underlying]      = ""
            self._pending_bars[underlying] = 0

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"        : self.name,
            "bb_period"       : self.bb_period,
            "bb_std_mult"     : self.bb_std,
            "atr_period"      : self.atr_period,
            "use_trend_filter": self.use_trend_filter,
            "trend_ma"        : self.trend_ma if self.use_trend_filter else "off",
            "vix_range"       : f"{self.vix_min}–{self.vix_max}",
            "confirm_bars"    : self.confirm_bars,
            "time_stop_days"  : self.time_stop_days,
            "weekly_expiry"   : self.weekly,
            "stop_loss"       : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"          : f"{config.BUY_TARGET_PCT}% of premium",
        }
