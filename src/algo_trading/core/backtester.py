"""
algo_trading.core.backtester — Core backtesting engine.

Simulates trading day by day. For each date:
  1. Check open positions for SL / target / expiry exits
  2. Call strategy.generate_signals() for new signals
  3. Execute entry signals (size position, price option via B-S)
  4. Record all trades and equity curve

Outputs a list of closed Trade records for the reporter.
"""

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from config import settings
from algo_trading.core.pricing import (
    bs_price,
    bs_greeks,
    get_atm_strike,
    get_strike_by_delta,
    time_to_expiry,
    calculate_transaction_cost,
    next_expiry,
)
from algo_trading.core.risk_manager import RiskManager
from algo_trading.strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)

# ── Bhavcopy real data lookup (optional) ─────────────────────────
_chain_lookup = None


def _get_chain_lookup():
    """Lazy-load the bhavcopy ChainLookup if BHAVCOPY_FOLDER is configured."""
    global _chain_lookup
    if _chain_lookup is not None:
        return _chain_lookup
    if not settings.BHAVCOPY_FOLDER:
        return None
    folder = os.path.abspath(settings.BHAVCOPY_FOLDER)
    if not os.path.isdir(folder):
        logger.warning(f"BHAVCOPY_FOLDER not found: {folder}. Using Black-Scholes.")
        return None
    try:
        from algo_trading.data.ingestion import load_bhavcopy_folder, ChainLookup
        logger.info(f"Loading bhavcopy chain data from {folder}...")
        chain = load_bhavcopy_folder(folder, symbol=settings.BHAVCOPY_SYMBOL,
                                     pattern="fo*bhav.csv.zip")
        _chain_lookup = ChainLookup(chain)
        logger.info("Bhavcopy chain loaded. Real premiums will be used.")
    except Exception as e:
        logger.warning(f"Failed to load bhavcopy data: {e}. Falling back to Black-Scholes.")
        _chain_lookup = None
    return _chain_lookup


# ─────────────────────────────────────────────────────────────────
# Trade dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    """Represents one completed options trade."""
    id             : str
    underlying     : str
    option_type    : str           # CE | PE
    direction      : str           # long | short
    strike         : float
    expiry         : date
    entry_date     : date
    exit_date      : Optional[date] = None
    entry_premium  : float = 0.0
    exit_premium   : float = 0.0
    lots           : int   = 1
    lot_size       : int   = 25
    entry_cost     : float = 0.0   # transaction cost at entry
    exit_cost      : float = 0.0   # transaction cost at exit
    exit_reason    : str   = ""    # target | sl | expiry | signal
    entry_spot     : float = 0.0
    exit_spot      : float = 0.0
    entry_delta    : float = 0.0
    entry_iv       : float = 0.0
    entry_theta    : float = 0.0   # theta per day at entry (₹)
    entry_meta     : dict = field(default_factory=dict)

    # Computed after close
    gross_pnl      : float = 0.0
    net_pnl        : float = 0.0
    pnl_pct        : float = 0.0
    held_days      : int   = 0

    def close(
        self,
        exit_date: date,
        exit_premium: float,
        exit_spot: float,
        exit_reason: str,
        exit_cost: float,
    ) -> None:
        self.exit_date    = exit_date
        self.exit_premium = exit_premium
        self.exit_spot    = exit_spot
        self.exit_reason  = exit_reason
        self.exit_cost    = exit_cost
        self.held_days    = (exit_date - self.entry_date).days

        multiplier = 1 if self.direction == "long" else -1
        self.gross_pnl = (
            multiplier
            * (self.exit_premium - self.entry_premium)
            * self.lots
            * self.lot_size
        )
        self.net_pnl   = self.gross_pnl - self.entry_cost - self.exit_cost
        ref_premium    = self.entry_premium if self.entry_premium != 0 else 1
        self.pnl_pct   = (self.net_pnl / (self.entry_premium * self.lots * self.lot_size)) * 100


# ─────────────────────────────────────────────────────────────────
# Backtester
# ─────────────────────────────────────────────────────────────────

class Backtester:
    """
    Event-driven backtester for options strategies.

    Parameters
    ----------
    strategy      : A BaseStrategy instance
    data          : Dict { ticker: DataFrame(OHLCV + VIX) }
    start         : Backtest start date
    end           : Backtest end date
    starting_capital : Initial capital in ₹
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        data: dict[str, pd.DataFrame],
        start: date = settings.BACKTEST_START,
        end:   date = settings.BACKTEST_END,
        starting_capital: float = settings.STARTING_CAPITAL,
    ):
        self.strategy  = strategy
        self.data      = data
        self.start     = start
        self.end       = end
        self.rm        = RiskManager(starting_capital)

        self.open_trades  : dict[str, Trade] = {}  # id → Trade
        self.closed_trades: list[Trade]      = []
        self.equity_curve : list[dict]       = []  # {date, capital}

    def run(self) -> list[Trade]:
        """
        Run the full backtest. Returns list of closed Trade objects.
        """
        self.rm.reset()
        self.open_trades   = {}
        self.closed_trades = []
        self.equity_curve  = []

        all_dates = self._get_trading_dates()
        logger.info(
            f"Backtesting {self.strategy.name} | "
            f"{self.start} → {self.end} | {len(all_dates)} trading days"
        )

        self._date_index: dict[str, dict] = {}
        for ticker, df in self.data.items():
            self._date_index[ticker] = {
                d: i for i, d in enumerate(df.index.date)
            }

        for current_date in all_dates:
            self._process_day(current_date)

        for trade_id in list(self.open_trades.keys()):
            self._force_close(trade_id, all_dates[-1])

        logger.info(
            f"Backtest complete: {len(self.closed_trades)} trades | "
            f"Final capital ₹{self.rm.capital:,.0f}"
        )
        return self.closed_trades

    # ── Day processing ────────────────────────────────────────────

    def _process_day(self, current_date: date) -> None:
        for trade_id in list(self.open_trades.keys()):
            self._check_exits(trade_id, current_date)

        for ticker, df in self.data.items():
            date_map = self._date_index.get(ticker, {})
            pos = date_map.get(current_date)
            if pos is None:
                continue
            slice_df = df.iloc[: pos + 1]
            slice_df.attrs["ticker"] = ticker
            vix = slice_df.get("VIX", pd.Series(dtype=float))

            signals = self.strategy.generate_signals(slice_df, vix, current_date)

            for sig in signals:
                if sig.signal_type == "exit":
                    self._handle_exit_signal(sig, current_date)
                else:
                    self._handle_entry_signal(sig, current_date, slice_df)

        self.equity_curve.append({
            "date":    current_date,
            "capital": self.rm.capital,
        })

    # ── Entry ─────────────────────────────────────────────────────

    def _handle_entry_signal(
        self,
        sig: Signal,
        current_date: date,
        df: pd.DataFrame,
    ) -> None:
        allowed, reason = self.rm.can_open_trade()
        if not allowed:
            logger.debug(f"Entry blocked on {current_date}: {reason}")
            return

        spot     = float(df["Close"].iloc[-1])
        vix_val  = float(df["VIX"].iloc[-1]) if "VIX" in df.columns else 15.0
        sigma    = vix_val / 100.0
        expiry   = sig.expiry or self._default_expiry(current_date)
        T        = time_to_expiry(current_date, expiry)

        if sig.strike == 0.0:
            target_delta = sig.meta.get("target_delta", None)
            if target_delta and sig.direction == "short":
                strike = get_strike_by_delta(
                    spot, T, settings.RISK_FREE_RATE, sigma,
                    target_delta, sig.option_type,
                    step=self._strike_step(sig.underlying),
                )
            else:
                strike = get_atm_strike(spot, self._strike_step(sig.underlying))
        else:
            strike = sig.strike

        lookup   = _get_chain_lookup()
        real_px  = None
        if lookup and lookup.has_data_for(current_date):
            real_strike = lookup.nearest_strike(strike)
            real_expiry = lookup.nearest_expiry(current_date, min_days=0)
            if real_expiry:
                real_px = lookup.premium(current_date, real_expiry, real_strike, sig.option_type)
                if real_px:
                    strike = real_strike
                    expiry = real_expiry.date() if hasattr(real_expiry, 'date') else real_expiry

        if real_px:
            premium_raw = real_px
            logger.debug(f"Real premium used: {sig.option_type} {strike} = ₹{real_px:.2f}")
        else:
            premium_raw = bs_price(spot, strike, T, settings.RISK_FREE_RATE, sigma, sig.option_type)

        slippage = premium_raw * settings.SLIPPAGE_PCT / 100.0
        entry_premium = premium_raw + slippage if sig.direction == "long" else premium_raw - slippage
        entry_premium = max(entry_premium, 0.01)

        lot_size = settings.LOT_SIZES.get(sig.underlying, settings.DEFAULT_LOT_SIZE)
        lots     = self.rm.position_size(entry_premium, lot_size)

        entry_side = "buy" if sig.direction == "long" else "sell"
        entry_cost = calculate_transaction_cost(entry_premium, lot_size, lots, entry_side)
        greeks     = bs_greeks(spot, strike, T, settings.RISK_FREE_RATE, sigma, sig.option_type)

        trade = Trade(
            id            = str(uuid.uuid4())[:8],
            underlying    = sig.underlying,
            option_type   = sig.option_type,
            direction     = sig.direction,
            strike        = strike,
            expiry        = expiry,
            entry_date    = current_date,
            entry_premium = entry_premium,
            lots          = lots,
            lot_size      = lot_size,
            entry_cost    = entry_cost,
            entry_spot    = spot,
            entry_delta   = greeks["delta"],
            entry_iv      = vix_val,
            entry_theta   = greeks["theta"],
            entry_meta    = dict(sig.meta),
        )

        self.open_trades[trade.id] = trade
        self.rm.register_open(trade.id)

        logger.info(
            f"ENTRY  {current_date} | {sig.underlying} {sig.option_type} "
            f"{strike} {expiry} | {sig.direction} | "
            f"₹{entry_premium:.2f} × {lots}L | IV={vix_val:.1f}%"
        )

    # ── Exit ──────────────────────────────────────────────────────

    def _check_exits(self, trade_id: str, current_date: date) -> None:
        trade = self.open_trades[trade_id]
        df    = self.data.get(trade.underlying)
        if df is None:
            return

        date_map = self._date_index.get(trade.underlying, {})
        pos = date_map.get(current_date)
        if pos is None:
            return

        row = df.iloc[pos]
        spot    = float(row["Close"])
        vix_val = float(row["VIX"]) if "VIX" in df.columns else trade.entry_iv
        sigma   = vix_val / 100.0
        T       = time_to_expiry(current_date, trade.expiry)

        days_to_expiry = (trade.expiry - current_date).days
        if days_to_expiry <= settings.DAYS_BEFORE_EXPIRY_EXIT:
            self._close_trade(trade_id, current_date, spot, sigma, T, "expiry")
            return

        current_premium = bs_price(
            spot, trade.strike, T, settings.RISK_FREE_RATE, sigma, trade.option_type
        )

        should_exit, reason = self.rm.check_exit(
            trade.direction, trade.entry_premium, current_premium
        )
        if should_exit:
            self._close_trade(trade_id, current_date, spot, sigma, T, reason)

    def _handle_exit_signal(self, sig: Signal, current_date: date) -> None:
        for trade_id, trade in list(self.open_trades.items()):
            if (
                trade.underlying   == sig.underlying
                and trade.option_type == sig.option_type
                and trade.direction   == sig.direction
            ):
                df = self.data.get(trade.underlying)
                if df is None:
                    continue
                date_map = self._date_index.get(trade.underlying, {})
                pos = date_map.get(current_date)
                if pos is None:
                    continue
                row = df.iloc[pos]
                spot    = float(row["Close"])
                vix_val = float(row["VIX"]) if "VIX" in df.columns else trade.entry_iv
                sigma   = vix_val / 100.0
                T       = time_to_expiry(current_date, trade.expiry)
                self._close_trade(trade_id, current_date, spot, sigma, T, sig.exit_reason)

    def _close_trade(
        self,
        trade_id: str,
        current_date: date,
        spot: float,
        sigma: float,
        T: float,
        reason: str,
    ) -> None:
        trade = self.open_trades.pop(trade_id)

        exit_premium_raw = None
        lookup = _get_chain_lookup()
        if lookup and lookup.has_data_for(current_date):
            real_expiry = lookup.nearest_expiry(current_date, min_days=0)
            if real_expiry:
                exit_premium_raw = lookup.premium(
                    current_date, real_expiry, trade.strike, trade.option_type
                )

        if not exit_premium_raw:
            exit_premium_raw = bs_price(
                spot, trade.strike, T, settings.RISK_FREE_RATE, sigma, trade.option_type
            )

        slippage = exit_premium_raw * settings.SLIPPAGE_PCT / 100.0
        exit_premium = (
            exit_premium_raw - slippage
            if trade.direction == "long"
            else exit_premium_raw + slippage
        )
        exit_premium = max(exit_premium, 0.01)

        exit_cost = calculate_transaction_cost(
            exit_premium, trade.lot_size, trade.lots,
            "sell" if trade.direction == "long" else "buy"
        )

        trade.close(current_date, exit_premium, spot, reason, exit_cost)
        self.closed_trades.append(trade)
        self.rm.register_close(trade_id, trade.net_pnl)
        self.strategy.on_trade_closed(trade)

        logger.info(
            f"EXIT   {current_date} | {trade.underlying} {trade.option_type} "
            f"{trade.strike} | {reason} | "
            f"₹{trade.entry_premium:.2f}→₹{exit_premium:.2f} | "
            f"P&L ₹{trade.net_pnl:+,.0f} ({trade.pnl_pct:+.1f}%)"
        )

    def _force_close(self, trade_id: str, last_date: date) -> None:
        trade = self.open_trades.get(trade_id)
        if not trade:
            return
        df = self.data.get(trade.underlying)
        if df is None:
            return
        date_map = self._date_index.get(trade.underlying, {})
        pos = date_map.get(last_date)
        if pos is None:
            return
        row = df.iloc[pos]
        spot = float(row["Close"])
        vix  = float(row["VIX"]) if "VIX" in df.columns else trade.entry_iv
        T    = time_to_expiry(last_date, trade.expiry)
        self._close_trade(trade_id, last_date, spot, vix / 100, T, "backtest_end")

    # ── Helpers ───────────────────────────────────────────────────

    def _get_trading_dates(self) -> list[date]:
        all_dates = set()
        for df in self.data.values():
            dates = set(df.loc[
                (df.index.date >= self.start) & (df.index.date <= self.end)
            ].index.date)
            if not all_dates:
                all_dates = dates
            else:
                all_dates &= dates
        return sorted(all_dates)

    def _default_expiry(self, from_date: date):
        return next_expiry(from_date, weekly=False)

    def _strike_step(self, underlying: str) -> float:
        return settings.STRIKE_STEPS.get(underlying, 50.0)

    def get_equity_curve(self) -> pd.DataFrame:
        return pd.DataFrame(self.equity_curve).set_index("date")
