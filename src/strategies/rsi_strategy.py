"""
rsi_strategy.py — RSI Reversal Options Strategy.

Logic:
  - Compute RSI on closing prices (default period 14)
  - RSI crosses UP through oversold threshold (e.g. 35) → market bouncing
    → BUY ATM CE (expect upward move)
  - RSI crosses DOWN through overbought threshold (e.g. 65) → market topping
    → BUY ATM PE (expect downward move)
  - Exit on SL/target (handled by backtester) or time stop

This is a mean-reversion / momentum-confirmation strategy:
  - We enter AFTER RSI shows exhaustion and starts reversing
  - Complements the MA crossover strategy which is slower to signal
  - Fires more frequently — 3-5x more entries than MA crossover
  - Uses weekly expiry by default (shorter hold, less theta decay)
"""

import pandas as pd
import numpy as np
from datetime import date

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import config
from src.strategies.base_strategy import BaseStrategy, Signal
from src.options_pricing import next_expiry


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute RSI using Wilder's smoothing method.
    Returns a Series of RSI values (0-100), same index as close.
    """
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)

    # Wilder smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)  # fill NaN with neutral 50


class RSIStrategy(BaseStrategy):
    """
    RSI-based options entry strategy.

    Enters when RSI reverses from extreme levels — catching momentum
    resumption after pullbacks/bounces.

    Parameters
    ----------
    rsi_period      : RSI lookback period (default 14)
    oversold        : RSI level considered oversold — buy CE on cross up (default 35)
    overbought      : RSI level considered overbought — buy PE on cross down (default 65)
    confirm_bars    : Bars RSI must hold past threshold before entry (default 1)
    time_stop_days  : Exit if not profitable within N days (default 10)
    weekly          : Use weekly expiry (default True — faster cycle)
    """

    def __init__(
        self,
        rsi_period     : int   = config.RSI_PERIOD,
        oversold       : float = config.RSI_OVERSOLD,
        overbought     : float = config.RSI_OVERBOUGHT,
        confirm_bars   : int   = config.RSI_CONFIRM_BARS,
        time_stop_days : int   = config.RSI_TIME_STOP_DAYS,
        weekly         : bool  = True,
    ):
        self.rsi_period     = rsi_period
        self.oversold       = oversold
        self.overbought     = overbought
        self.confirm_bars   = confirm_bars
        self.time_stop_days = time_stop_days
        self.weekly         = weekly

        # State per underlying
        self._prev_signal  : dict[str, str]  = {}   # "long_ce" | "long_pe" | "none"
        self._entry_date   : dict[str, date] = {}
        self._pending      : dict[str, str]  = {}   # "ce" | "pe" | ""
        self._pending_bars : dict[str, int]  = {}

    @property
    def name(self) -> str:
        return (
            f"rsi_{self.rsi_period}_"
            f"ob{int(self.overbought)}_os{int(self.oversold)}"
            f"{'_w' if self.weekly else '_m'}"
        )

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:

        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = self.rsi_period * 3 + self.confirm_bars + 2
        if len(data) < min_bars:
            return signals

        close   = data["Close"]
        rsi     = _compute_rsi(close, self.rsi_period)

        rsi_now  = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])

        if pd.isna(rsi_now) or pd.isna(rsi_prev):
            return signals

        spot   = float(close.iloc[-1])
        expiry = next_expiry(current_date, weekly=self.weekly)
        prev   = self._prev_signal.get(underlying, "none")

        # ── Time stop check ──────────────────────────────────────
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

        # ── Detect RSI crossovers ─────────────────────────────────
        # Bullish: RSI was below oversold and just crossed above it
        bullish_cross = (rsi_prev <= self.oversold) and (rsi_now > self.oversold)
        # Bearish: RSI was above overbought and just crossed below it
        bearish_cross = (rsi_prev >= self.overbought) and (rsi_now < self.overbought)

        pending      = self._pending.get(underlying, "")
        pending_bars = self._pending_bars.get(underlying, 0)

        # ── Confirmation logic ────────────────────────────────────
        if bullish_cross and prev != "long_ce":
            self._pending[underlying]      = "ce"
            self._pending_bars[underlying] = 1
        elif bearish_cross and prev != "long_pe":
            self._pending[underlying]      = "pe"
            self._pending_bars[underlying] = 1
        elif pending:
            # Check the RSI is still on the right side
            still_bull = pending == "ce" and rsi_now > self.oversold
            still_bear = pending == "pe" and rsi_now < self.overbought
            if still_bull or still_bear:
                self._pending_bars[underlying] = pending_bars + 1
            else:
                self._pending[underlying]      = ""
                self._pending_bars[underlying] = 0

        confirmed_ce = (
            self._pending.get(underlying) == "ce"
            and self._pending_bars.get(underlying, 0) >= self.confirm_bars
            and prev != "long_ce"
        )
        confirmed_pe = (
            self._pending.get(underlying) == "pe"
            and self._pending_bars.get(underlying, 0) >= self.confirm_bars
            and prev != "long_pe"
        )

        # ── Emit signals ──────────────────────────────────────────
        if confirmed_ce:
            if prev == "long_pe":
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction="long", option_type="PE",
                    signal_type="exit", exit_reason="signal",
                    meta={"reason": "RSI bullish, exit PE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="CE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "rsi":   round(rsi_now, 2),
                    "close": round(spot, 2),
                    "level": f"oversold cross up ({self.oversold})",
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
                    meta={"reason": "RSI bearish, exit CE"},
                ))
            signals.append(Signal(
                date=current_date, underlying=underlying,
                direction="long", option_type="PE",
                strike=0.0, expiry=expiry, signal_type="entry",
                meta={
                    "rsi":   round(rsi_now, 2),
                    "close": round(spot, 2),
                    "level": f"overbought cross down ({self.overbought})",
                },
            ))
            self._prev_signal[underlying]  = "long_pe"
            self._entry_date[underlying]   = current_date
            self._pending[underlying]      = ""
            self._pending_bars[underlying] = 0

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"      : self.name,
            "rsi_period"    : self.rsi_period,
            "oversold"      : self.oversold,
            "overbought"    : self.overbought,
            "confirm_bars"  : self.confirm_bars,
            "time_stop_days": self.time_stop_days,
            "weekly_expiry" : self.weekly,
            "stop_loss"     : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"        : f"{config.BUY_TARGET_PCT}% of premium",
        }
