"""
mean_reversion.py — Short Strangle on High IV Strategy.

Logic:
  - Compute IV percentile using a rolling window of India VIX values
  - When IV percentile > threshold → SELL OTM strangle (short CE + short PE)
  - Exit when IV percentile drops below exit threshold, or SL/target hit
  - Uses monthly expiry, multi-day positional holds

This is a non-directional, short premium strategy (we collect premium).
Risk is theoretically unlimited on the upside (CE leg). Risk management
stop loss is critical here.
"""

import pandas as pd
from datetime import date

import config
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.core.pricing import next_expiry, get_strike_by_delta


@register_strategy("mean_rev", "mean_reversion")
class MeanReversionStrategy(BaseStrategy):
    """
    Short Strangle on elevated IV (sell CE + sell PE).

    Parameters
    ----------
    iv_entry_pct  : Enter when IV is above this percentile (default 70)
    iv_exit_pct   : Exit when IV drops below this percentile (default 30)
    otm_delta     : Target delta for OTM strikes (default 0.20)
    iv_window     : Rolling window in days to compute IV percentile (default 252)
    weekly        : Use weekly expiry if True, monthly if False
    """

    def __init__(
        self,
        iv_entry_pct: float = config.MR_IV_PERCENTILE_ENTRY,
        iv_exit_pct:  float = config.MR_IV_PERCENTILE_EXIT,
        otm_delta:    float = config.MR_OTM_DELTA,
        iv_window:    int   = 252,
        weekly:       bool  = False,
    ):
        self.iv_entry_pct = iv_entry_pct
        self.iv_exit_pct  = iv_exit_pct
        self.otm_delta    = otm_delta
        self.iv_window    = iv_window
        self.weekly       = weekly
        self._in_trade: dict[str, bool] = {}

    @property
    def name(self) -> str:
        return f"mean_rev_strangle_{int(self.iv_entry_pct)}_{int(self.iv_exit_pct)}"

    def generate_signals(
        self,
        data: pd.DataFrame,
        vix: pd.Series,
        current_date: date,
    ) -> list[Signal]:
        signals = []
        underlying = data.attrs.get("ticker", "UNKNOWN")

        if len(vix.dropna()) < self.iv_window:
            return signals

        current_vix = vix.iloc[-1]
        if pd.isna(current_vix):
            return signals

        # Rolling IV percentile
        rolling_vix   = vix.dropna().iloc[-self.iv_window:]
        iv_percentile = (rolling_vix < current_vix).sum() / len(rolling_vix) * 100

        in_trade = self._in_trade.get(underlying, False)
        spot     = float(data["Close"].iloc[-1])
        expiry   = next_expiry(current_date, weekly=self.weekly)
        T        = max((expiry - current_date).days / 365.0, 1/365)
        sigma    = current_vix / 100.0

        if not in_trade and iv_percentile >= self.iv_entry_pct:
            # Enter short strangle — sell OTM CE and OTM PE
            signals.append(Signal(
                date=current_date,
                underlying=underlying,
                direction="short",
                option_type="CE",
                strike=0.0,  # backtester will call get_strike_by_delta
                expiry=expiry,
                signal_type="entry",
                meta={
                    "iv_percentile": round(float(iv_percentile), 1),
                    "current_vix":   round(float(current_vix), 2),
                    "target_delta":  self.otm_delta,
                    "spot":          round(spot, 2),
                },
            ))
            signals.append(Signal(
                date=current_date,
                underlying=underlying,
                direction="short",
                option_type="PE",
                strike=0.0,
                expiry=expiry,
                signal_type="entry",
                meta={
                    "iv_percentile": round(float(iv_percentile), 1),
                    "current_vix":   round(float(current_vix), 2),
                    "target_delta":  self.otm_delta,
                    "spot":          round(spot, 2),
                },
            ))
            self._in_trade[underlying] = True

        elif in_trade and iv_percentile <= self.iv_exit_pct:
            # Exit strangle — IV has reverted
            for opt_type in ("CE", "PE"):
                signals.append(Signal(
                    date=current_date,
                    underlying=underlying,
                    direction="short",
                    option_type=opt_type,
                    signal_type="exit",
                    exit_reason="signal",
                    meta={
                        "iv_percentile": round(float(iv_percentile), 1),
                        "reason": "IV reverted below exit threshold",
                    },
                ))
            self._in_trade[underlying] = False

        return signals

    def get_params(self) -> dict:
        return {
            "strategy"     : self.name,
            "iv_entry_pct" : self.iv_entry_pct,
            "iv_exit_pct"  : self.iv_exit_pct,
            "otm_delta"    : self.otm_delta,
            "iv_window"    : self.iv_window,
            "weekly"       : self.weekly,
            "stop_loss"    : f"{config.SELL_STOP_LOSS_PCT}% of premium received",
            "target"       : f"{config.SELL_TARGET_PCT}% premium decay",
        }
