"""
orb_strategy.py — Opening Range Breakout (ORB) Strategy.

Logic:
  - The "opening range" is approximated from daily OHLCV data.
    True intraday ORB uses the first N minutes; on daily bars we proxy it
    using the previous day's close as the reference and the current day's
    Open as the range anchor.

  Breakout detection (daily proxy):
    - If today's Close > Open * (1 + breakout_pct/100)  → BUY CE  (upside breakout)
    - If today's Close < Open * (1 - breakout_pct/100)  → BUY PE  (downside breakout)

  Filters:
    - VIX range: skip panic (too gappy) or dead-calm days
    - ADX: enter only on trending days (ADX > threshold)
    - Breakout must not be too wide (gap_max_pct) — avoids chasing exhaustion moves

  This naturally complements TrendFollowing (MA-lag vs. intraday momentum) and
  works best on strong trending days where the open sets the direction for the day.

  Exit:
    - Opposite breakout signal
    - Time stop after time_stop_days
    - SL / target enforced by backtester (BUY_STOP_LOSS_PCT / BUY_TARGET_PCT)
"""

import pandas as pd
import numpy as np
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ADX (same implementation as trend_following.py)."""
    high  = high.reset_index(drop=True)
    low   = low.reset_index(drop=True)
    close = close.reset_index(drop=True)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    def _wilder(s: pd.Series, p: int) -> pd.Series:
        r = s.copy().astype(float)
        r.iloc[:p] = np.nan
        r.iloc[p - 1] = s.iloc[:p].mean()
        alpha = 1.0 / p
        for i in range(p, len(s)):
            r.iloc[i] = r.iloc[i - 1] * (1 - alpha) + s.iloc[i] * alpha
        return r

    atr      = _wilder(pd.Series(tr),              period)
    plus_di  = 100 * _wilder(pd.Series(plus_dm),  period) / atr
    minus_di = 100 * _wilder(pd.Series(minus_dm), period) / atr
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _wilder(dx.fillna(0), period)
    adx.index = high.index
    return adx


@register_strategy("orb")
class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout — directional, momentum-based.

    Uses daily OHLCV where Open is treated as the range anchor.
    A sustained close N% above/below open signals a directional breakout.

    Complements TrendFollowingStrategy: ORB fires on the same day as the
    move (no MA lag), while trend-following confirms over multiple bars.

    Parameters
    ----------
    breakout_pct   : Close must be this % above/below Open to qualify (default 0.5)
    gap_max_pct    : Skip if gap from prev_close to open is already > this % (default 1.5)
    adx_period     : ADX period for trend-strength filter (default 14)
    adx_threshold  : Minimum ADX for entry (0 = disabled) (default 0)
    use_adx_filter : Enable/disable ADX filter (default False)
    vix_min        : Skip if VIX below this — options too cheap (default 11.0)
    vix_max        : Skip if VIX above this — panic regime (default 35.0)
    time_stop_days : Exit if not profitable within N days (default 5)
    weekly         : Use weekly expiry (default True)
    """

    def __init__(
        self,
        breakout_pct   : float = config.ORB_BREAKOUT_PCT,
        gap_max_pct    : float = config.ORB_GAP_MAX_PCT,
        adx_period     : int   = 14,
        adx_threshold  : float = config.ORB_ADX_THRESHOLD,
        use_adx_filter : bool  = config.ORB_USE_ADX_FILTER,
        vix_min        : float = config.BB_VIX_MIN,
        vix_max        : float = config.BB_VIX_MAX,
        time_stop_days : int   = config.ORB_TIME_STOP_DAYS,
        weekly         : bool  = True,
    ):
        self.breakout_pct   = breakout_pct
        self.gap_max_pct    = gap_max_pct
        self.adx_period     = adx_period
        self.adx_threshold  = adx_threshold
        self.use_adx_filter = use_adx_filter
        self.vix_min        = vix_min
        self.vix_max        = vix_max
        self.time_stop_days = time_stop_days
        self.weekly         = weekly

        self._prev_signal : dict[str, str]  = {}
        self._entry_date  : dict[str, date] = {}

    @property
    def name(self) -> str:
        adx_tag = f"_adx{self.adx_threshold:.0f}" if self.use_adx_filter else ""
        return f"orb_{self.breakout_pct:.1f}pct{adx_tag}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = max(self.adx_period * 2 + 5, 30)
        if len(data) < min_bars:
            return signals

        close = data["Close"]
        high  = data["High"]
        low   = data["Low"]
        open_ = data["Open"]

        spot       = float(close.iloc[-1])
        today_open = float(open_.iloc[-1])

        if today_open <= 0:
            return signals

        # ── VIX regime filter ─────────────────────────────────────
        vix_now = float(vix.iloc[-1]) if not vix.empty else 15.0
        if pd.isna(vix_now):
            vix_now = 15.0
        if vix_now < self.vix_min or vix_now > self.vix_max:
            return signals

        # ── Gap filter: skip if already gapped too far at open ───
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else today_open
        gap_pct    = abs(today_open - prev_close) / prev_close * 100
        if gap_pct > self.gap_max_pct:
            return signals

        # ── ADX filter ────────────────────────────────────────────
        if self.use_adx_filter:
            adx = _compute_adx(high, low, close, self.adx_period)
            adx_val = float(adx.iloc[-1])
            if pd.isna(adx_val) or adx_val < self.adx_threshold:
                return signals

        # ── Breakout detection ────────────────────────────────────
        up_threshold   = today_open * (1.0 + self.breakout_pct / 100.0)
        down_threshold = today_open * (1.0 - self.breakout_pct / 100.0)

        bullish_breakout = spot > up_threshold
        bearish_breakout = spot < down_threshold

        prev = self._prev_signal.get(underlying, "none")

        # ── Time stop ─────────────────────────────────────────────
        if self.time_stop_days > 0:
            entry_dt = self._entry_date.get(underlying)
            if entry_dt and (current_date - entry_dt).days >= self.time_stop_days:
                if prev in ("long_ce", "long_pe"):
                    opt = "CE" if prev == "long_ce" else "PE"
                    signals.append(Signal(
                        date=current_date, underlying=underlying,
                        direction="long", option_type=opt,
                        signal_type="exit", exit_reason="time_stop",
                        meta={"days_held": (current_date - entry_dt).days},
                    ))
                    self._prev_signal[underlying] = "none"
                    self._entry_date[underlying]  = None
                    return signals

        expiry = self.resolve_expiry(data, current_date, weekly=self.weekly)

        # ── Emit signals ──────────────────────────────────────────
        if bullish_breakout and prev != "long_ce":
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "ORB flipped bullish"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "open"         : round(today_open, 2),
                    "close"        : round(spot, 2),
                    "breakout_pct" : self.breakout_pct,
                    "actual_move"  : round((spot - today_open) / today_open * 100, 2),
                    "vix"          : round(vix_now, 2),
                    "trigger"      : "ORB bullish breakout",
                },
            ))
            self._prev_signal[underlying] = "long_ce"
            self._entry_date[underlying]  = current_date

        elif bearish_breakout and prev != "long_pe":
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "ORB flipped bearish"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "open"         : round(today_open, 2),
                    "close"        : round(spot, 2),
                    "breakout_pct" : self.breakout_pct,
                    "actual_move"  : round((spot - today_open) / today_open * 100, 2),
                    "vix"          : round(vix_now, 2),
                    "trigger"      : "ORB bearish breakout",
                },
            ))
            self._prev_signal[underlying] = "long_pe"
            self._entry_date[underlying]  = current_date

        elif (
            prev == "long_ce" and not bullish_breakout
            or prev == "long_pe" and not bearish_breakout
        ):
            # Breakout failed — no follow-through, emit exit
            opt = "CE" if prev == "long_ce" else "PE"
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type=opt,
                signal_type="exit", exit_reason="signal",
                meta={"reason": "ORB breakout failed, no follow-through"},
            ))
            self._prev_signal[underlying] = "none"
            self._entry_date[underlying]  = None

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"      : self.name,
            "breakout_pct"  : self.breakout_pct,
            "gap_max_pct"   : self.gap_max_pct,
            "use_adx_filter": self.use_adx_filter,
            "adx_threshold" : self.adx_threshold if self.use_adx_filter else "off",
            "vix_range"     : f"{self.vix_min}–{self.vix_max}",
            "time_stop_days": self.time_stop_days,
            "weekly_expiry" : self.weekly,
            "stop_loss"     : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"        : f"{config.BUY_TARGET_PCT}% of premium",
        }
