"""
gap_fade.py — Gap Fade Strategy.

A "gap" occurs when the market opens significantly above or below the
previous day's close. Small gaps (~0.3–0.8%) are common and often fill
immediately. Larger gaps frequently see partial reversion in index
options because:
  1. Retail panic-buys/sells the open.
  2. Institutions fade the imbalance.
  3. Index options have tight spreads → cheap to enter a counter-gap bet.

Logic:
  - Compute gap = (today_open - yesterday_close) / yesterday_close * 100
  - If gap > gap_min_pct (gap up, buy-side panic) → BUY PE (fade the gap up)
  - If gap < -gap_min_pct (gap down, sell-side panic) → BUY CE (fade the gap down)
  - "No follow-through" confirmation: if price has already partly filled the gap
    by end of day (close moved back toward prev_close) → stronger signal
  - Exit: short time stop (gaps either fill quickly or fail), or opposite gap

Filters:
  - Gap must not exceed gap_max_pct (don't fade genuine news gaps)
  - VIX regime: moderate VIX ensures options are liquid
  - Trend filter (optional): only fade gaps that go against the trend
    (e.g. gap up in a downtrend is more likely to fade)

Best on index options (Nifty, BankNifty): liquid, tight spreads, cheap weekly.
"""

import pandas as pd
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry


@register_strategy("gap_fade")
class GapFadeStrategy(BaseStrategy):
    """
    Fade opening gaps beyond a threshold when there is no follow-through.

    Parameters
    ----------
    gap_min_pct       : Minimum gap % to consider (default 0.5)
    gap_max_pct       : Maximum gap % — above this is genuine news (default 2.0)
    require_no_follow : Require partial gap fill by close (default True)
                        If True, the close must be moving back toward prev_close.
    trend_filter      : Only fade gaps that go against the 200MA trend (default False)
    trend_ma          : Period for trend MA filter (default 200)
    vix_min           : Skip if VIX below this (default 11.0)
    vix_max           : Skip if VIX above this (default 35.0)
    time_stop_days    : Exit if not profitable within N days (default 4)
    weekly            : Use weekly expiry (default True)
    """

    def __init__(
        self,
        gap_min_pct       : float = config.GAP_MIN_PCT,
        gap_max_pct       : float = config.GAP_MAX_PCT,
        require_no_follow : bool  = config.GAP_REQUIRE_NO_FOLLOW,
        trend_filter      : bool  = config.GAP_TREND_FILTER,
        trend_ma          : int   = 200,
        vix_min           : float = config.BB_VIX_MIN,
        vix_max           : float = config.BB_VIX_MAX,
        time_stop_days    : int   = config.GAP_TIME_STOP_DAYS,
        weekly            : bool  = True,
    ):
        self.gap_min_pct       = gap_min_pct
        self.gap_max_pct       = gap_max_pct
        self.require_no_follow = require_no_follow
        self.trend_filter      = trend_filter
        self.trend_ma          = trend_ma
        self.vix_min           = vix_min
        self.vix_max           = vix_max
        self.time_stop_days    = time_stop_days
        self.weekly            = weekly

        self._prev_signal : dict[str, str]  = {}
        self._entry_date  : dict[str, date] = {}

    @property
    def name(self) -> str:
        trf = "_trf" if self.trend_filter else ""
        nf  = "_nf" if self.require_no_follow else ""
        return f"gap_fade_{self.gap_min_pct:.1f}_{self.gap_max_pct:.1f}{trf}{nf}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = self.trend_ma + 5 if self.trend_filter else 10
        if len(data) < min_bars:
            return signals

        close = data["Close"]
        open_ = data["Open"]

        today_open   = float(open_.iloc[-1])
        today_close  = float(close.iloc[-1])
        prev_close   = float(close.iloc[-2]) if len(close) >= 2 else today_open

        if prev_close <= 0 or today_open <= 0:
            return signals

        # ── VIX regime filter ─────────────────────────────────────
        vix_now = float(vix.iloc[-1]) if not vix.empty else 15.0
        if pd.isna(vix_now):
            vix_now = 15.0
        if vix_now < self.vix_min or vix_now > self.vix_max:
            return signals

        # ── Gap calculation ───────────────────────────────────────
        gap_pct = (today_open - prev_close) / prev_close * 100  # signed

        gap_up   = gap_pct >  self.gap_min_pct
        gap_down = gap_pct < -self.gap_min_pct

        # Filter: gap must not be too large (news-driven, don't fade)
        if abs(gap_pct) > self.gap_max_pct:
            return signals

        if not gap_up and not gap_down:
            return signals

        # ── No follow-through confirmation ────────────────────────
        # For a gap up: follow-through = close > open (gap extending)
        #               no follow-through = close <= open (gap fading)
        if self.require_no_follow:
            if gap_up   and today_close > today_open:
                return signals  # price extended upward — not fading
            if gap_down and today_close < today_open:
                return signals  # price extended downward — not fading

        # ── Trend filter ──────────────────────────────────────────
        # Only fade gaps that go AGAINST the major trend
        # (e.g. gap up in a downtrend is more reliable to fade)
        if self.trend_filter:
            trend_ma_val = float(close.rolling(self.trend_ma).mean().iloc[-1])
            if not pd.isna(trend_ma_val):
                in_uptrend = today_close > trend_ma_val
                # Gap up in uptrend → trend supports, don't fade
                if gap_up and in_uptrend:
                    return signals
                # Gap down in downtrend → trend supports, don't fade
                if gap_down and not in_uptrend:
                    return signals

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

        expiry = next_expiry(current_date, weekly=self.weekly)
        meta_base = {
            "gap_pct"   : round(gap_pct, 3),
            "prev_close": round(prev_close, 2),
            "open"      : round(today_open, 2),
            "close"     : round(today_close, 2),
            "vix"       : round(vix_now, 2),
        }

        # ── Gap up → Fade with PE ─────────────────────────────────
        if gap_up and prev != "long_pe":
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "gap direction changed"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={**meta_base, "trigger": f"Gap up {gap_pct:.2f}% — fade"},
            ))
            self._prev_signal[underlying] = "long_pe"
            self._entry_date[underlying]  = current_date

        # ── Gap down → Fade with CE ───────────────────────────────
        elif gap_down and prev != "long_ce":
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "gap direction changed"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={**meta_base, "trigger": f"Gap down {gap_pct:.2f}% — fade"},
            ))
            self._prev_signal[underlying] = "long_ce"
            self._entry_date[underlying]  = current_date

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"         : self.name,
            "gap_min_pct"      : self.gap_min_pct,
            "gap_max_pct"      : self.gap_max_pct,
            "require_no_follow": self.require_no_follow,
            "trend_filter"     : self.trend_filter,
            "trend_ma"         : self.trend_ma if self.trend_filter else "off",
            "vix_range"        : f"{self.vix_min}–{self.vix_max}",
            "time_stop_days"   : self.time_stop_days,
            "weekly_expiry"    : self.weekly,
            "stop_loss"        : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"           : f"{config.BUY_TARGET_PCT}% of premium",
        }
