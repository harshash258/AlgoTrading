"""
vwap_reversion.py — VWAP Reversion / Breakout Strategy.

VWAP (Volume-Weighted Average Price) is the intraday fairness reference for
institutional order flow. On daily data we compute a rolling VWAP over a
lookback window, then add standard-deviation bands (similar to Bollinger Bands
but anchored to volume-weighted price instead of simple moving average).

Two modes:
  reversion  — Buy CE/PE when price stretches away from VWAP and reverts back.
               Classic mean-reversion: sell the extension, fade the spike.
               Signal: price crosses back inside the VWAP band from outside.

  breakout   — Buy in the direction of a strong VWAP break.
               Used on trending days where price persistently stays above/below VWAP.
               Signal: price closes decisively outside the band.

Default is reversion mode (mirrors the project's existing mean-reversion theme but
with a different signal source — volume-weighted price vs. IV percentile).

VWAP calculation (daily proxy):
  - True VWAP resets each session. On daily bars we use a rolling window:
    VWAP = sum(Close * Volume, window) / sum(Volume, window)
  - Upper/lower bands = VWAP ± std_mult × rolling_std(Close, window)

Difference from BollingerBandStrategy:
  - BB uses simple price SMA; VWAP weights by volume (volume-weighted mean).
  - Institutions trade against VWAP — this strategy uses the same reference.
  - Different signal source = genuinely uncorrelated entries.
"""

import pandas as pd
import numpy as np
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry


@register_strategy("vwap_rev", "vwap_brk", "vwap")
class VWAPReversionStrategy(BaseStrategy):
    """
    Rolling VWAP ± σ-band reversion or breakout strategy.

    Parameters
    ----------
    vwap_window    : Lookback bars for rolling VWAP calculation (default 20)
    std_mult       : Standard deviation multiplier for bands (default 1.5)
    mode           : "reversion" (fade extensions) or "breakout" (follow breaks)
    vix_min        : Skip if VIX below this (default 11.0)
    vix_max        : Skip if VIX above this (default 35.0)
    time_stop_days : Exit if not profitable within N days (default 8)
    weekly         : Use weekly expiry (default True)
    confirm_bars   : Price must be outside band for N bars before signal (default 1)
    """

    def __init__(
        self,
        vwap_window    : int   = config.VWAP_WINDOW,
        std_mult       : float = config.VWAP_STD_MULT,
        mode           : str   = config.VWAP_MODE,
        vix_min        : float = config.BB_VIX_MIN,
        vix_max        : float = config.BB_VIX_MAX,
        time_stop_days : int   = config.VWAP_TIME_STOP_DAYS,
        weekly         : bool  = True,
        confirm_bars   : int   = 1,
    ):
        assert mode in ("reversion", "breakout"), \
            f"mode must be 'reversion' or 'breakout', got '{mode}'"
        self.vwap_window    = vwap_window
        self.std_mult       = std_mult
        self.mode           = mode
        self.vix_min        = vix_min
        self.vix_max        = vix_max
        self.time_stop_days = time_stop_days
        self.weekly         = weekly
        self.confirm_bars   = confirm_bars

        self._prev_signal  : dict[str, str]  = {}
        self._entry_date   : dict[str, date] = {}
        self._outside_count: dict[str, int]  = {}  # bars spent outside band

    @property
    def name(self) -> str:
        return f"vwap_{self.mode}_w{self.vwap_window}_std{self.std_mult:.1f}"

    def _compute_vwap_bands(
        self,
        close: pd.Series,
        volume: pd.Series,
        window: int,
        std_mult: float,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Compute rolling VWAP and upper/lower σ-bands.

        Returns (vwap, upper_band, lower_band) as pd.Series.
        """
        close  = close.reset_index(drop=True)
        volume = volume.reset_index(drop=True)

        pv         = close * volume
        rolling_pv = pv.rolling(window).sum()
        rolling_v  = volume.rolling(window).sum().replace(0, np.nan)
        vwap       = rolling_pv / rolling_v

        std        = close.rolling(window).std()
        upper_band = vwap + std_mult * std
        lower_band = vwap - std_mult * std

        return vwap, upper_band, lower_band

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = self.vwap_window + self.confirm_bars + 5
        if len(data) < min_bars:
            return signals

        close  = data["Close"]
        volume = data.get("Volume", pd.Series(np.ones(len(data))))

        spot = float(close.iloc[-1])

        # ── VIX regime filter ─────────────────────────────────────
        vix_now = float(vix.iloc[-1]) if not vix.empty else 15.0
        if pd.isna(vix_now):
            vix_now = 15.0
        if vix_now < self.vix_min or vix_now > self.vix_max:
            return signals

        # ── Compute VWAP bands ────────────────────────────────────
        vwap, upper, lower = self._compute_vwap_bands(close, volume, self.vwap_window, self.std_mult)

        vwap_now  = float(vwap.iloc[-1])
        upper_now = float(upper.iloc[-1])
        lower_now = float(lower.iloc[-1])

        if any(pd.isna(x) for x in (vwap_now, upper_now, lower_now)):
            return signals

        # Previous bar's close for crossover detection
        spot_prev  = float(close.iloc[-2]) if len(close) >= 2 else spot
        upper_prev = float(upper.iloc[-2]) if len(upper) >= 2 else upper_now
        lower_prev = float(lower.iloc[-2]) if len(lower) >= 2 else lower_now

        prev  = self._prev_signal.get(underlying, "none")
        count = self._outside_count.get(underlying, 0)

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
                    self._prev_signal[underlying]   = "none"
                    self._entry_date[underlying]    = None
                    self._outside_count[underlying] = 0
                    return signals

        expiry = next_expiry(current_date, weekly=self.weekly)

        # ── REVERSION MODE ────────────────────────────────────────
        # Signal fires when price crosses BACK inside the band
        if self.mode == "reversion":
            # Was outside lower band, now crossing back in → buy CE (price recovering)
            below_lower_prev = spot_prev < lower_prev
            back_above_lower = spot >= lower_now

            # Was outside upper band, now crossing back in → buy PE (price mean-reverting)
            above_upper_prev = spot_prev > upper_prev
            back_below_upper = spot <= upper_now

            ce_signal = below_lower_prev and back_above_lower
            pe_signal = above_upper_prev and back_below_upper

            # Update outside-band counter for confirmation
            if spot < lower_now:
                self._outside_count[underlying] = count + 1
            elif spot > upper_now:
                self._outside_count[underlying] = count + 1
            else:
                self._outside_count[underlying] = 0

            # Require price to have spent at least confirm_bars outside band
            if count < self.confirm_bars:
                ce_signal = False
                pe_signal = False

        # ── BREAKOUT MODE ─────────────────────────────────────────
        # Signal fires when price breaks AND holds outside the band
        else:
            above_upper = spot > upper_now
            below_lower = spot < lower_now

            if above_upper:
                self._outside_count[underlying] = count + 1
            elif below_lower:
                self._outside_count[underlying] = count + 1
            else:
                self._outside_count[underlying] = 0

            confirmed = self._outside_count.get(underlying, 0) >= self.confirm_bars
            ce_signal = above_upper and confirmed and prev != "long_ce"
            pe_signal = below_lower and confirmed and prev != "long_pe"

        meta_base = {
            "vwap"      : round(vwap_now, 2),
            "upper_band": round(upper_now, 2),
            "lower_band": round(lower_now, 2),
            "spot"      : round(spot, 2),
            "vix"       : round(vix_now, 2),
            "mode"      : self.mode,
        }

        if ce_signal and prev != "long_ce":
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "VWAP signal flipped bullish"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={**meta_base, "trigger": f"VWAP {self.mode} CE"},
            ))
            self._prev_signal[underlying]   = "long_ce"
            self._entry_date[underlying]    = current_date
            self._outside_count[underlying] = 0

        elif pe_signal and prev != "long_pe":
            if prev == "long_ce":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="CE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "VWAP signal flipped bearish"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={**meta_base, "trigger": f"VWAP {self.mode} PE"},
            ))
            self._prev_signal[underlying]   = "long_pe"
            self._entry_date[underlying]    = current_date
            self._outside_count[underlying] = 0

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"      : self.name,
            "vwap_window"   : self.vwap_window,
            "std_mult"      : self.std_mult,
            "mode"          : self.mode,
            "confirm_bars"  : self.confirm_bars,
            "vix_range"     : f"{self.vix_min}–{self.vix_max}",
            "time_stop_days": self.time_stop_days,
            "weekly_expiry" : self.weekly,
            "stop_loss"     : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"        : f"{config.BUY_TARGET_PCT}% of premium",
        }
