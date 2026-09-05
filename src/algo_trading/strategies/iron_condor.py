"""
iron_condor.py — Delta-Neutral Iron Condor Strategy.

An Iron Condor is a market-neutral, short-volatility income strategy:
  - SELL OTM CE (body, ~0.20-0.25 delta)   → collect premium
  - BUY  far OTM CE (wing, ~0.10 delta)    → cap unlimited CE risk
  - SELL OTM PE (body, ~0.20-0.25 delta)   → collect premium
  - BUY  far OTM PE (wing, ~0.10 delta)    → cap unlimited PE risk

Net: receive credit, profit if underlying stays between the short strikes.
Max profit = net credit collected (all four legs expire worthless).
Max loss  = wing width − net credit (capped by long wings).

When to use:
  - Choppy / range-bound days: no clear trend direction.
  - High IV environment (sell vol when expensive, similar to MeanReversion short strangle).
  - Differentiated from the existing short strangle by the protective wings:
    defined-risk on both sides, acceptable for higher position sizes.

Relationship to existing strategies:
  - MeanReversionStrategy: short strangle (unlimited risk) on high IV.
  - IronCondorStrategy:   defined-risk short condor on high IV + chop regime.
  - LongStraddleStrategy: buys vol on low IV — the natural pair.

Entry conditions:
  - IV percentile > iv_entry_pct (selling expensive vol)
  - ADX < adx_choppy_threshold (market is NOT trending — condor wins in chop)
  - VIX in a moderate range

Exit:
  - IV drops below iv_exit_pct (premium decayed — take profit early)
  - Time stop: close position before theta-decay reverses
  - Backtester handles structure-level SL/target across the four legs

Four Signal objects are emitted per entry (one per leg).
The backtester treats each as an independent Trade, so SL/target work
per-leg as normal.
"""

import pandas as pd
import numpy as np
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry, get_strike_by_delta


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ADX (duplicated locally to keep strategy self-contained)."""
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


@register_strategy("iron_condor")
class IronCondorStrategy(BaseStrategy):
    """
    Delta-neutral Iron Condor — market-neutral income on high-IV, choppy days.

    Emits 4 Signals per entry (short CE body, long CE wing, short PE body, long PE wing).

    Parameters
    ----------
    iv_entry_pct        : Enter when IV percentile > this (default 60)
    iv_exit_pct         : Exit when IV percentile < this (default 35)
    body_delta          : Delta for short body strikes (default 0.25)
    wing_delta          : Delta for long wing strikes (default 0.10)
    iv_window           : Rolling window for IV percentile (default 252)
    adx_period          : ADX calculation period (default 14)
    adx_choppy_threshold: Only enter if ADX < this (chop regime) (default 22)
    use_adx_filter      : Enable/disable ADX chop filter (default True)
    vix_min             : Skip if VIX below this (default 13.0)
    vix_max             : Skip if VIX above this (default 40.0)
    time_stop_days      : Exit after N days regardless (default 15)
    weekly              : Use weekly expiry (default False — monthly for more theta)
    """

    def __init__(
        self,
        iv_entry_pct         : float = config.IC_IV_ENTRY_PCT,
        iv_exit_pct          : float = config.IC_IV_EXIT_PCT,
        body_delta           : float = config.IC_BODY_DELTA,
        wing_delta           : float = config.IC_WING_DELTA,
        iv_window            : int   = 252,
        adx_period           : int   = 14,
        adx_choppy_threshold : float = config.IC_ADX_CHOPPY_THRESHOLD,
        use_adx_filter       : bool  = config.IC_USE_ADX_FILTER,
        vix_min              : float = config.IC_VIX_MIN,
        vix_max              : float = config.IC_VIX_MAX,
        time_stop_days       : int   = config.IC_TIME_STOP_DAYS,
        weekly               : bool  = False,
    ):
        self.iv_entry_pct         = iv_entry_pct
        self.iv_exit_pct          = iv_exit_pct
        self.body_delta           = body_delta
        self.wing_delta           = wing_delta
        self.iv_window            = iv_window
        self.adx_period           = adx_period
        self.adx_choppy_threshold = adx_choppy_threshold
        self.use_adx_filter       = use_adx_filter
        self.vix_min              = vix_min
        self.vix_max              = vix_max
        self.time_stop_days       = time_stop_days
        self.weekly               = weekly

        self._in_trade  : dict[str, bool] = {}
        self._entry_date: dict[str, date] = {}

    @property
    def name(self) -> str:
        adx_tag = f"_adx{self.adx_choppy_threshold:.0f}" if self.use_adx_filter else ""
        return (
            f"iron_condor_iv{int(self.iv_entry_pct)}"
            f"_b{self.body_delta:.2f}_w{self.wing_delta:.2f}"
            f"{adx_tag}"
        )

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals    = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        min_bars = max(self.iv_window, self.adx_period * 2 + 5, 30)
        if len(vix.dropna()) < self.iv_window:
            return signals
        if len(data) < self.adx_period * 2 + 5:
            return signals

        current_vix = float(vix.iloc[-1])
        if pd.isna(current_vix):
            return signals

        # ── VIX range filter ──────────────────────────────────────
        if current_vix < self.vix_min or current_vix > self.vix_max:
            return signals

        # ── IV percentile ─────────────────────────────────────────
        rolling_vix   = vix.dropna().iloc[-self.iv_window:]
        iv_percentile = (rolling_vix < current_vix).sum() / len(rolling_vix) * 100

        # ── ADX chop filter ───────────────────────────────────────
        if self.use_adx_filter:
            adx     = _compute_adx(data["High"], data["Low"], data["Close"], self.adx_period)
            adx_val = float(adx.iloc[-1])
            if pd.isna(adx_val) or adx_val >= self.adx_choppy_threshold:
                # Market is trending — iron condor loses on directional moves
                return signals
        else:
            adx_val = None

        in_trade = self._in_trade.get(underlying, False)

        spot  = float(data["Close"].iloc[-1])
        expiry = next_expiry(current_date, weekly=self.weekly)
        T      = max((expiry - current_date).days / 365.0, 1 / 365)
        sigma  = current_vix / 100.0
        step   = config.STRIKE_STEPS.get(underlying, 50.0)

        # ── Time stop ─────────────────────────────────────────────
        if in_trade and self.time_stop_days > 0:
            entry_dt = self._entry_date.get(underlying)
            if entry_dt and (current_date - entry_dt).days >= self.time_stop_days:
                # Exit all four legs
                group_id = f"{underlying}:{self.name}"
                for direction, opt_type in (
                    ("short", "CE"), ("long", "CE"),
                    ("short", "PE"), ("long", "PE"),
                ):
                    signals.append(Signal(
                        date=current_date, underlying=underlying,
                        direction=direction, option_type=opt_type,
                        signal_type="exit", exit_reason="time_stop",
                        group_id=group_id,
                        structure_type="iron_condor",
                        meta={"days_held": (current_date - entry_dt).days},
                    ))
                self._in_trade[underlying]  = False
                self._entry_date[underlying] = None
                return signals

        # ── Entry ─────────────────────────────────────────────────
        if not in_trade and iv_percentile >= self.iv_entry_pct:
            # Compute strikes for all four legs
            short_ce = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.body_delta, "CE", step=step,
            )
            long_ce  = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.wing_delta, "CE", step=step,
            )
            short_pe = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.body_delta, "PE", step=step,
            )
            long_pe  = get_strike_by_delta(
                spot, T, config.RISK_FREE_RATE, sigma,
                self.wing_delta, "PE", step=step,
            )

            meta_base = {
                "iv_percentile"  : round(float(iv_percentile), 1),
                "current_vix"    : round(current_vix, 2),
                "spot"           : round(spot, 2),
                "body_delta"     : self.body_delta,
                "wing_delta"     : self.wing_delta,
                "adx"            : round(float(adx_val), 2) if adx_val is not None else "off",
                "trigger"        : f"Iron Condor — IV pct={iv_percentile:.1f}% (high), chop regime",
            }
            group_id = f"{underlying}:{self.name}:{current_date.isoformat()}"

            # Four legs: short body CE, long wing CE, short body PE, long wing PE
            for direction, opt_type, strike in (
                ("short", "CE", short_ce),
                ("long",  "CE", long_ce),
                ("short", "PE", short_pe),
                ("long",  "PE", long_pe),
            ):
                leg_tag = f"{'short' if direction == 'short' else 'long'} {opt_type} "
                leg_tag += f"{'body' if direction == 'short' else 'wing'}"
                signals.append(Signal(
                    date=current_date,
                    underlying=underlying,
                    direction=direction,
                    option_type=opt_type,
                    strike=strike,
                    expiry=expiry,
                    signal_type="entry",
                    group_id=group_id,
                    structure_type="iron_condor",
                    meta={**meta_base, "leg": leg_tag},
                ))

            self._in_trade[underlying]  = True
            self._entry_date[underlying] = current_date

        # ── Exit: IV decayed / risk-off ───────────────────────────
        elif in_trade and iv_percentile <= self.iv_exit_pct:
            group_id = f"{underlying}:{self.name}"
            for direction, opt_type in (
                ("short", "CE"), ("long", "CE"),
                ("short", "PE"), ("long", "PE"),
            ):
                signals.append(Signal(
                    date=current_date, underlying=underlying,
                    direction=direction, option_type=opt_type,
                    signal_type="exit", exit_reason="signal",
                    group_id=group_id,
                    structure_type="iron_condor",
                    meta={
                        "iv_percentile" : round(float(iv_percentile), 1),
                        "reason"        : "IV decayed below exit threshold",
                    },
                ))
            self._in_trade[underlying]  = False
            self._entry_date[underlying] = None

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"             : self.name,
            "iv_entry_pct"         : self.iv_entry_pct,
            "iv_exit_pct"          : self.iv_exit_pct,
            "body_delta"           : self.body_delta,
            "wing_delta"           : self.wing_delta,
            "iv_window"            : self.iv_window,
            "adx_choppy_threshold" : self.adx_choppy_threshold if self.use_adx_filter else "off",
            "use_adx_filter"       : self.use_adx_filter,
            "vix_range"            : f"{self.vix_min}–{self.vix_max}",
            "time_stop_days"       : self.time_stop_days,
            "weekly_expiry"        : self.weekly,
            "stop_loss"            : f"{config.SELL_STOP_LOSS_PCT}% (short legs)",
            "target"               : f"{config.SELL_TARGET_PCT}% premium decay (short legs)",
        }
