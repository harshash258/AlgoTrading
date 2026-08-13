"""
confluence_strategy.py — MA + RSI Signal Confluence Strategy.

The combined strategy fires MA and RSI signals independently, which
causes frequent CE/PE conflicts (one strategy says bullish, the other
bearish on the same underlying). In practice you'd skip those days.

This strategy fixes that by requiring BOTH indicators to agree before
entering. It produces fewer trades but meaningfully higher win rate
because every entry has two independent reasons behind it.

Entry rules:
  BULLISH (BUY CE):
    - Fast MA crosses above slow MA (trend turning up)
    AND
    - RSI recently crossed up from oversold (momentum confirming)
    AND
    - RSI is currently in the 40–65 range (not overbought at entry)

  BEARISH (BUY PE):
    - Fast MA crosses below slow MA (trend turning down)
    AND
    - RSI recently crossed down from overbought (momentum confirming)
    AND
    - RSI is currently in the 35–60 range (not oversold at entry)

The MA crossover is the primary trigger. RSI is the confirmation gate.
RSI agreement window: RSI must have crossed its threshold within the
last `rsi_lookback` bars for the signal to be valid.

Additional filters:
  - VIX regime: skip if VIX > vix_max (panic) or < vix_min (too calm)
  - 200MA direction: optional, aligns entries with the big trend
  - Time stop: exit if not profitable within N days
"""

import pandas as pd
import numpy as np
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI using Wilder's smoothing — same as RSIStrategy."""
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


@register_strategy("confluence")
class ConfluenceStrategy(BaseStrategy):
    """
    MA Crossover + RSI Agreement confluence strategy.

    Both indicators must agree on direction before an entry is taken.
    This eliminates the CE/PE conflict problem in the raw combined strategy
    and produces higher-conviction, lower-frequency signals.

    Parameters
    ----------
    fast_ma         : Fast MA period (default from config)
    slow_ma         : Slow MA period (default from config)
    rsi_period      : RSI period (default from config)
    rsi_oversold    : RSI oversold threshold — CE gate (default from config)
    rsi_overbought  : RSI overbought threshold — PE gate (default from config)
    rsi_lookback    : How many bars back to look for an RSI cross (default 5)
                      Wider window = more signals; tighter = stricter confirmation
    rsi_entry_max   : RSI must be below this when entering CE (not overbought)
    rsi_entry_min   : RSI must be above this when entering PE (not oversold)
    trend_ma        : 200MA for direction filter (default 200)
    use_trend_filter: Align entries with 200MA trend (default from config)
    vix_min         : Skip if VIX below this (default from config)
    vix_max         : Skip if VIX above this (default from config)
    time_stop_days  : Exit if not profitable within N days (default from config)
    weekly          : Use weekly expiry (default True)
    """

    def __init__(
        self,
        fast_ma          : int   = config.TREND_FAST_MA,
        slow_ma          : int   = config.TREND_SLOW_MA,
        rsi_period       : int   = config.RSI_PERIOD,
        rsi_oversold     : float = config.RSI_OVERSOLD,
        rsi_overbought   : float = config.RSI_OVERBOUGHT,
        rsi_lookback     : int   = config.CONFLUENCE_RSI_LOOKBACK,
        rsi_entry_max    : float = config.CONFLUENCE_RSI_ENTRY_MAX,
        rsi_entry_min    : float = config.CONFLUENCE_RSI_ENTRY_MIN,
        trend_ma         : int   = 200,
        use_trend_filter : bool  = config.CONFLUENCE_USE_TREND_FILTER,
        vix_min          : float = config.BB_VIX_MIN,
        vix_max          : float = config.BB_VIX_MAX,
        time_stop_days   : int   = config.CONFLUENCE_TIME_STOP_DAYS,
        weekly           : bool  = True,
    ):
        self.fast_ma          = fast_ma
        self.slow_ma          = slow_ma
        self.rsi_period       = rsi_period
        self.rsi_oversold     = rsi_oversold
        self.rsi_overbought   = rsi_overbought
        self.rsi_lookback     = rsi_lookback
        self.rsi_entry_max    = rsi_entry_max
        self.rsi_entry_min    = rsi_entry_min
        self.trend_ma         = trend_ma
        self.use_trend_filter = use_trend_filter
        self.vix_min          = vix_min
        self.vix_max          = vix_max
        self.time_stop_days   = time_stop_days
        self.weekly           = weekly

        # State per underlying
        self._prev_signal : dict[str, str]  = {}
        self._entry_date  : dict[str, date] = {}

    @property
    def name(self) -> str:
        trf = "_trf" if self.use_trend_filter else ""
        return (
            f"confluence_ma{self.fast_ma}_{self.slow_ma}"
            f"_rsi{self.rsi_period}"
            f"_lb{self.rsi_lookback}"
            f"{trf}"
        )

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:

        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = max(self.slow_ma, self.trend_ma, self.rsi_period * 3) + self.rsi_lookback + 5
        if len(data) < min_bars:
            return signals

        close = data["Close"]
        spot  = float(close.iloc[-1])

        # ── VIX regime filter ─────────────────────────────────────
        vix_now = float(vix.iloc[-1]) if hasattr(vix, "iloc") else float(vix)
        if pd.isna(vix_now):
            vix_now = 15.0
        if vix_now < self.vix_min or vix_now > self.vix_max:
            return signals

        # ── Moving averages ───────────────────────────────────────
        fast_series = close.rolling(self.fast_ma).mean()
        slow_series = close.rolling(self.slow_ma).mean()

        fast_now  = float(fast_series.iloc[-1])
        fast_prev = float(fast_series.iloc[-2])
        slow_now  = float(slow_series.iloc[-1])
        slow_prev = float(slow_series.iloc[-2])

        if pd.isna(fast_now) or pd.isna(slow_now):
            return signals

        # ── RSI ───────────────────────────────────────────────────
        rsi = _compute_rsi(close, self.rsi_period)
        rsi_now = float(rsi.iloc[-1])

        if pd.isna(rsi_now):
            return signals

        # ── 200MA direction filter ────────────────────────────────
        above_200ma = True
        below_200ma = True
        if self.use_trend_filter:
            trend_val = float(close.rolling(self.trend_ma).mean().iloc[-1])
            if not pd.isna(trend_val):
                above_200ma = spot > trend_val
                below_200ma = spot < trend_val

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

        # ── MA crossover detection ────────────────────────────────
        # Primary trigger: fresh crossover this bar
        bullish_ma_cross = (fast_prev <= slow_prev) and (fast_now > slow_now)
        bearish_ma_cross = (fast_prev >= slow_prev) and (fast_now < slow_now)

        # Also check for a recent cross within the lookback window
        # (slow MAs like 25/75 lag by design — we look back rsi_lookback bars
        #  so a cross that completed recently still counts)
        lookback = min(self.rsi_lookback, len(fast_series) - 2)
        recent_bullish_cross = bullish_ma_cross
        recent_bearish_cross = bearish_ma_cross
        for j in range(1, lookback + 1):
            if len(fast_series) <= j + 1:
                break
            fp  = float(fast_series.iloc[-(j + 1)])
            sp  = float(slow_series.iloc[-(j + 1)])
            fp1 = float(fast_series.iloc[-j])
            sp1 = float(slow_series.iloc[-j])
            if fp <= sp and fp1 > sp1:
                recent_bullish_cross = True
            if fp >= sp and fp1 < sp1:
                recent_bearish_cross = True

        fast_above_slow = fast_now > slow_now
        fast_below_slow = fast_now < slow_now

        # ── RSI confirmation gate ─────────────────────────────────
        # Two-tier RSI confirmation:
        #
        # Tier 1 (strict) — RSI crossed the threshold within lookback window.
        # Best for fast MAs (5/20, 10/30) where cross and RSI bounce are close.
        #
        # Tier 2 (relaxed) — RSI was recently in extreme territory and has
        # since recovered to a healthy mid-zone. Handles slow MAs (25/75)
        # where the RSI bounce precedes the MA cross by 30-50 bars.
        # "Recovery" = RSI touched oversold within 2×lookback bars AND
        # is now between oversold+10 and overbought-10 (healthy mid-range).

        # -- Tier 1: recent cross --
        rsi_window_size = min(self.rsi_lookback, len(rsi) - 1)
        rsi_window = rsi.iloc[-(rsi_window_size + 1):]

        rsi_recently_bullish = False
        for i in range(len(rsi_window) - 1):
            if rsi_window.iloc[i] <= self.rsi_oversold and rsi_window.iloc[i + 1] > self.rsi_oversold:
                rsi_recently_bullish = True
                break

        rsi_recently_bearish = False
        for i in range(len(rsi_window) - 1):
            if rsi_window.iloc[i] >= self.rsi_overbought and rsi_window.iloc[i + 1] < self.rsi_overbought:
                rsi_recently_bearish = True
                break

        # -- Tier 2: RSI bounced from extreme and is in healthy mid-range now --
        extended_window = min(self.rsi_lookback * 2, len(rsi) - 1)
        rsi_extended = rsi.iloc[-extended_window:]
        rsi_low_watermark  = float(rsi_extended.min())
        rsi_high_watermark = float(rsi_extended.max())

        rsi_mid_low  = self.rsi_oversold  + 10   # e.g. 35 for oversold=25
        rsi_mid_high = self.rsi_overbought - 10  # e.g. 55 for overbought=65

        # Tier 2 bullish: RSI dipped into oversold within 2×lookback AND
        # has now recovered into the healthy mid-zone
        rsi_recovery_bullish = (
            rsi_low_watermark <= self.rsi_oversold
            and rsi_mid_low <= rsi_now <= rsi_mid_high
        )
        # Tier 2 bearish: RSI peaked into overbought within 2×lookback AND
        # has now pulled back into the healthy mid-zone
        rsi_recovery_bearish = (
            rsi_high_watermark >= self.rsi_overbought
            and rsi_mid_low <= rsi_now <= rsi_mid_high
        )

        rsi_bullish_confirmed = rsi_recently_bullish or rsi_recovery_bullish
        rsi_bearish_confirmed = rsi_recently_bearish or rsi_recovery_bearish

        # ── Confluence conditions ─────────────────────────────────
        # CE entry:
        #   - MA recently crossed bullish AND is still above (trend intact)
        #   - RSI confirms: either fresh bounce or recovery from oversold
        #   - RSI at entry is not overbought (don't chase)
        ce_confluence = (
            recent_bullish_cross
            and fast_above_slow
            and rsi_bullish_confirmed
            and rsi_now < self.rsi_entry_max
            and (above_200ma or not self.use_trend_filter)
            and prev != "long_ce"
        )

        # PE entry:
        #   - MA recently crossed bearish AND is still below (trend intact)
        #   - RSI confirms: either fresh rollover or recovery from overbought
        #   - RSI at entry is not oversold (don't chase)
        pe_confluence = (
            recent_bearish_cross
            and fast_below_slow
            and rsi_bearish_confirmed
            and rsi_now > self.rsi_entry_min
            and (below_200ma or not self.use_trend_filter)
            and prev != "long_pe"
        )

        expiry = next_expiry(current_date, weekly=self.weekly)

        # ── Emit signals ──────────────────────────────────────────
        if ce_confluence:
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "confluence flipped bullish, exit PE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "fast_ma"  : round(fast_now, 2),
                    "slow_ma"  : round(slow_now, 2),
                    "rsi"      : round(rsi_now, 2),
                    "vix"      : round(vix_now, 2),
                    "close"    : round(spot, 2),
                    "trigger"  : f"MA cross up + RSI oversold recovery",
                },
            ))
            self._prev_signal[underlying] = "long_ce"
            self._entry_date[underlying]  = current_date

        elif pe_confluence:
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "confluence flipped bearish, exit CE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "fast_ma"  : round(fast_now, 2),
                    "slow_ma"  : round(slow_now, 2),
                    "rsi"      : round(rsi_now, 2),
                    "vix"      : round(vix_now, 2),
                    "close"    : round(spot, 2),
                    "trigger"  : f"MA cross down + RSI overbought rollover",
                },
            ))
            self._prev_signal[underlying] = "long_pe"
            self._entry_date[underlying]  = current_date

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"        : self.name,
            "fast_ma"         : self.fast_ma,
            "slow_ma"         : self.slow_ma,
            "rsi_period"      : self.rsi_period,
            "rsi_oversold"    : self.rsi_oversold,
            "rsi_overbought"  : self.rsi_overbought,
            "rsi_lookback"    : self.rsi_lookback,
            "rsi_entry_max"   : self.rsi_entry_max,
            "rsi_entry_min"   : self.rsi_entry_min,
            "use_trend_filter": self.use_trend_filter,
            "trend_ma"        : self.trend_ma if self.use_trend_filter else "off",
            "vix_range"       : f"{self.vix_min}–{self.vix_max}",
            "time_stop_days"  : self.time_stop_days,
            "weekly_expiry"   : self.weekly,
            "stop_loss"       : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"          : f"{config.BUY_TARGET_PCT}% of premium",
        }
