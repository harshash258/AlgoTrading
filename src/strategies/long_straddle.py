"""
long_straddle.py — Long Straddle / Strangle on Low IV.

This is the natural complement to MeanReversionStrategy (short strangle):
  - MeanReversion SELLS premium when IV is HIGH (expect compression)
  - LongStraddle   BUYS  premium when IV is LOW  (expect expansion)

Together they form a vol pair: short vol when expensive, long vol when cheap.

Long Straddle logic:
  - Buy ATM CE + ATM PE simultaneously (straddle)
  - Entry trigger: IV percentile below iv_entry_pct (vol is cheap)
  - Exit: IV percentile rises above iv_exit_pct (vol has expanded) OR time stop
  - Profit from large directional moves OR volatility expansion

Long Strangle variant (otm_delta > 0):
  - Buy OTM CE + OTM PE instead of ATM (cheaper, needs bigger move)
  - Controlled by otm_delta parameter (0 = straddle / ATM, >0 = strangle/OTM)

Key insight:
  - This fills the book's biggest gap: ALL existing strategies sell or trade
    direction. Nothing currently buys volatility outright.
  - Historically works well before earnings, budget events, RBI policy days —
    on low-IV days right before a known catalyst.
"""

import pandas as pd
from datetime import date

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import config
from src.strategies.base_strategy import BaseStrategy, Signal
from src.options_pricing import next_expiry, get_strike_by_delta, get_atm_strike


class LongStraddleStrategy(BaseStrategy):
    """
    Long Straddle / Strangle when implied volatility is cheap.

    Buys both CE and PE simultaneously — profits from any large move
    OR from volatility expansion (vega gain).

    Parameters
    ----------
    iv_entry_pct  : Enter when IV percentile is BELOW this (default 30)
    iv_exit_pct   : Exit when IV percentile rises ABOVE this (default 60)
    otm_delta     : Delta for OTM legs (0 = ATM straddle, 0.25 = strangle)
    iv_window     : Rolling window to compute IV percentile (default 252)
    time_stop_days: Exit after N days regardless (default 10)
    weekly        : Use weekly expiry (default True — cheaper theta burn)
    vix_min       : Absolute VIX floor — don't buy when truly dead (default 10.0)
    vix_max       : Don't buy vol in extreme panic (default 30.0)
    """

    def __init__(
        self,
        iv_entry_pct   : float = config.STRADDLE_IV_ENTRY_PCT,
        iv_exit_pct    : float = config.STRADDLE_IV_EXIT_PCT,
        otm_delta      : float = config.STRADDLE_OTM_DELTA,
        iv_window      : int   = 252,
        time_stop_days : int   = config.STRADDLE_TIME_STOP_DAYS,
        weekly         : bool  = True,
        vix_min        : float = config.STRADDLE_VIX_MIN,
        vix_max        : float = config.STRADDLE_VIX_MAX,
    ):
        self.iv_entry_pct   = iv_entry_pct
        self.iv_exit_pct    = iv_exit_pct
        self.otm_delta      = otm_delta
        self.iv_window      = iv_window
        self.time_stop_days = time_stop_days
        self.weekly         = weekly
        self.vix_min        = vix_min
        self.vix_max        = vix_max

        self._in_trade : dict[str, bool] = {}
        self._entry_date: dict[str, date] = {}

    @property
    def name(self) -> str:
        variant = "straddle" if self.otm_delta == 0 else f"strangle_{self.otm_delta:.2f}d"
        return f"long_{variant}_iv{int(self.iv_entry_pct)}_{int(self.iv_exit_pct)}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        if len(vix.dropna()) < self.iv_window:
            return signals

        current_vix = float(vix.iloc[-1])
        if pd.isna(current_vix):
            return signals

        # Absolute VIX bounds — don't trade in dead markets or panic
        if current_vix < self.vix_min or current_vix > self.vix_max:
            return signals

        # IV percentile (same calculation as MeanReversionStrategy)
        rolling_vix   = vix.dropna().iloc[-self.iv_window:]
        iv_percentile = (rolling_vix < current_vix).sum() / len(rolling_vix) * 100

        in_trade   = self._in_trade.get(underlying, False)
        spot       = float(data["Close"].iloc[-1])
        expiry     = next_expiry(current_date, weekly=self.weekly)
        T          = max((expiry - current_date).days / 365.0, 1 / 365)
        sigma      = current_vix / 100.0
        step       = config.STRIKE_STEPS.get(underlying, 50.0)

        # Determine strikes
        if self.otm_delta > 0:
            # Strangle: OTM legs
            strike_ce = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.otm_delta, "CE", step=step,
            )
            strike_pe = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.otm_delta, "PE", step=step,
            )
        else:
            # Straddle: ATM
            strike_ce = get_atm_strike(spot, step)
            strike_pe = strike_ce

        # ── Time stop ─────────────────────────────────────────────
        if in_trade and self.time_stop_days > 0:
            entry_dt = self._entry_date.get(underlying)
            if entry_dt and (current_date - entry_dt).days >= self.time_stop_days:
                for opt_type in ("CE", "PE"):
                    signals.append(Signal(
                        date=current_date, underlying=underlying,
                        direction="long", option_type=opt_type,
                        signal_type="exit", exit_reason="time_stop",
                        meta={"days_held": (current_date - entry_dt).days},
                    ))
                self._in_trade[underlying]  = False
                self._entry_date[underlying] = None
                return signals

        # ── Entry: IV percentile is LOW → buy vol cheap ───────────
        if not in_trade and iv_percentile <= self.iv_entry_pct:
            variant = "straddle" if self.otm_delta == 0 else "strangle"
            for opt_type, strike in (("CE", strike_ce), ("PE", strike_pe)):
                signals.append(Signal(
                    date=current_date,
                    underlying=underlying,
                    direction="long",
                    option_type=opt_type,
                    strike=strike,
                    expiry=expiry,
                    signal_type="entry",
                    meta={
                        "iv_percentile" : round(float(iv_percentile), 1),
                        "current_vix"   : round(current_vix, 2),
                        "spot"          : round(spot, 2),
                        "variant"       : variant,
                        "otm_delta"     : self.otm_delta,
                        "trigger"       : f"IV percentile {iv_percentile:.1f}% ≤ {self.iv_entry_pct}% (vol cheap)",
                        # Mark both legs as part of the same paired trade so the
                        # conflict detector does not flag CE+PE as opposing signals.
                        "paired_legs"   : True,
                        "pair_type"     : variant,
                    },
                ))
            self._in_trade[underlying]  = True
            self._entry_date[underlying] = current_date

        # ── Exit: IV percentile has risen → take vol expansion profit
        elif in_trade and iv_percentile >= self.iv_exit_pct:
            for opt_type in ("CE", "PE"):
                signals.append(Signal(
                    date=current_date,
                    underlying=underlying,
                    direction="long",
                    option_type=opt_type,
                    signal_type="exit",
                    exit_reason="signal",
                    meta={
                        "iv_percentile" : round(float(iv_percentile), 1),
                        "reason"        : f"IV expanded above {self.iv_exit_pct}%",
                    },
                ))
            self._in_trade[underlying]  = False
            self._entry_date[underlying] = None

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"      : self.name,
            "iv_entry_pct"  : self.iv_entry_pct,
            "iv_exit_pct"   : self.iv_exit_pct,
            "otm_delta"     : self.otm_delta,
            "variant"       : "straddle" if self.otm_delta == 0 else "strangle",
            "iv_window"     : self.iv_window,
            "time_stop_days": self.time_stop_days,
            "vix_range"     : f"{self.vix_min}–{self.vix_max}",
            "weekly_expiry" : self.weekly,
            "stop_loss"     : f"{config.BUY_STOP_LOSS_PCT}% of premium",
            "target"        : f"{config.BUY_TARGET_PCT}% of premium",
        }
