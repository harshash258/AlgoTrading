"""Core backtesting engine for NSE option strategies."""

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

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

_chain_lookups: dict[str, object] = {}
_chain_lookup = None


def reset_chain_cache() -> None:
    """Clear cached per-underlying bhavcopy lookups."""
    _chain_lookups.clear()


def _bhavcopy_symbol_for(underlying: str) -> str:
    return getattr(settings, "BHAVCOPY_SYMBOLS", {}).get(
        underlying,
        getattr(settings, "BHAVCOPY_SYMBOL", "NIFTY"),
    )


def _get_chain_lookup(underlying: str | None = None):
    """Lazy-load and cache bhavcopy ChainLookup per NSE symbol."""
    if _chain_lookup is not None:
        return _chain_lookup

    nse_symbol = _bhavcopy_symbol_for(underlying or "")
    if nse_symbol in _chain_lookups:
        return _chain_lookups[nse_symbol]

    if not settings.BHAVCOPY_FOLDER:
        return None

    folder = os.path.abspath(settings.BHAVCOPY_FOLDER)
    if not os.path.isdir(folder):
        logger.warning("BHAVCOPY_FOLDER not found: %s. Using Black-Scholes.", folder)
        return None

    try:
        from algo_trading.data.ingestion import ChainLookup, load_bhavcopy_folder

        chain = load_bhavcopy_folder(folder, symbol=nse_symbol, pattern="fo*bhav.csv.zip")
        _chain_lookups[nse_symbol] = ChainLookup(chain)
    except Exception as exc:
        logger.warning("Failed to load bhavcopy for %s: %s. Using Black-Scholes.", nse_symbol, exc)
        _chain_lookups[nse_symbol] = None
    return _chain_lookups[nse_symbol]


@dataclass
class Trade:
    """Represents one completed option leg."""

    id: str
    underlying: str
    option_type: str
    direction: str
    strike: float
    expiry: date
    entry_date: date
    signal_date: date | None = None
    group_id: str = ""
    structure_type: str = "single"
    leg_label: str = ""
    exit_date: Optional[date] = None
    entry_premium: float = 0.0
    exit_premium: float = 0.0
    lots: int = 1
    lot_size: int = 25
    entry_cost: float = 0.0
    exit_cost: float = 0.0
    exit_reason: str = ""
    entry_spot: float = 0.0
    exit_spot: float = 0.0
    entry_delta: float = 0.0
    entry_iv: float = 0.0
    entry_theta: float = 0.0
    entry_meta: dict = field(default_factory=dict)
    pricing_source: str = "black_scholes"
    vix_source: str = "unknown"
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    pnl_pct: float = 0.0
    held_days: int = 0

    def close(
        self,
        exit_date: date,
        exit_premium: float,
        exit_spot: float,
        exit_reason: str,
        exit_cost: float,
    ) -> None:
        self.exit_date = exit_date
        self.exit_premium = exit_premium
        self.exit_spot = exit_spot
        self.exit_reason = exit_reason
        self.exit_cost = exit_cost
        self.held_days = (exit_date - self.entry_date).days
        multiplier = 1 if self.direction == "long" else -1
        self.gross_pnl = multiplier * (self.exit_premium - self.entry_premium) * self.lots * self.lot_size
        self.net_pnl = self.gross_pnl - self.entry_cost - self.exit_cost
        exposure = abs(self.entry_premium * self.lots * self.lot_size) or 1
        self.pnl_pct = self.net_pnl / exposure * 100


@dataclass
class Position:
    """One open option structure; single-leg trades are positions too."""

    id: str
    underlying: str
    structure_type: str
    signal_date: date
    entry_date: date
    legs: list[Trade] = field(default_factory=list)
    exit_date: date | None = None
    exit_reason: str = ""
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    pnl_pct: float = 0.0

    @property
    def notional_basis(self) -> float:
        return sum(abs(leg.entry_premium * leg.lots * leg.lot_size) for leg in self.legs) or 1

    @property
    def risk_side(self) -> str:
        return "short" if self.structure_type in {"short_strangle", "iron_condor"} else "long"

    def mark_closed(self, exit_date: date, reason: str) -> None:
        self.exit_date = exit_date
        self.exit_reason = reason
        self.gross_pnl = sum(leg.gross_pnl for leg in self.legs)
        self.net_pnl = sum(leg.net_pnl for leg in self.legs)
        self.pnl_pct = self.net_pnl / self.notional_basis * 100


class Backtester:
    """Event-driven options backtester with next-open and grouped-leg support."""

    def __init__(
        self,
        strategy: BaseStrategy,
        data: dict[str, pd.DataFrame],
        start: date = settings.BACKTEST_START,
        end: date = settings.BACKTEST_END,
        starting_capital: float = settings.STARTING_CAPITAL,
        execution_timing: str | None = None,
    ):
        self.strategy = strategy
        self.data = data
        self.start = start
        self.end = end
        self.execution_timing = execution_timing or getattr(settings, "EXECUTION_TIMING", "next_open")
        self.rm = RiskManager(starting_capital)
        self.open_trades: dict[str, Trade] = {}
        self.open_positions: dict[str, Position] = {}
        self.closed_trades: list[Trade] = []
        self.closed_positions: list[Position] = []
        self.pending_entries: dict[date, list[Signal]] = {}
        self.equity_curve: list[dict] = []

    def run(self) -> list[Trade]:
        self.rm.reset()
        self.open_trades = {}
        self.open_positions = {}
        self.closed_trades = []
        self.closed_positions = []
        self.pending_entries = {}
        self.equity_curve = []
        all_dates = self._get_trading_dates()
        if not all_dates:
            return []
        self._all_dates = all_dates
        self._date_index = {
            ticker: {d: i for i, d in enumerate(df.index.date)}
            for ticker, df in self.data.items()
        }

        for current_date in all_dates:
            self._process_day(current_date)

        for position_id in list(self.open_positions):
            self._close_position(position_id, all_dates[-1], "backtest_end")
        return self.closed_trades

    def _process_day(self, current_date: date) -> None:
        self._execute_pending_entries(current_date)
        for position_id in list(self.open_positions):
            self._check_position_exits(position_id, current_date)

        for ticker, df in self.data.items():
            pos = self._date_index.get(ticker, {}).get(current_date)
            if pos is None:
                continue
            slice_df = df.iloc[: pos + 1].copy()
            slice_df.attrs["ticker"] = ticker
            vix = slice_df.get("VIX", pd.Series(dtype=float))
            entry_signals = []
            for sig in self.strategy.generate_signals(slice_df, vix, current_date):
                if sig.signal_type == "exit":
                    self._handle_exit_signal(sig, current_date)
                else:
                    entry_signals.append(sig)
            self._handle_entry_signals(entry_signals, current_date)

        self.equity_curve.append({"date": current_date, "capital": self.rm.capital})

    def _handle_entry_signals(self, signals: list[Signal], signal_date: date) -> None:
        if not signals:
            return
        timing = signals[0].execution_timing or self.execution_timing
        if timing == "same_day_close":
            self._open_signal_groups(signals, signal_date, signal_date, "close")
            return
        entry_date = self._next_trading_date(signal_date)
        if entry_date is None:
            return
        for sig in signals:
            sig.meta = {**sig.meta, "scheduled_entry_date": entry_date.isoformat()}
            self.pending_entries.setdefault(entry_date, []).append(sig)

    def _execute_pending_entries(self, current_date: date) -> None:
        signals = self.pending_entries.pop(current_date, [])
        by_signal_date: dict[date, list[Signal]] = {}
        for sig in signals:
            by_signal_date.setdefault(sig.date, []).append(sig)
        for signal_date, grouped in by_signal_date.items():
            self._open_signal_groups(grouped, signal_date, current_date, "open")

    def _open_signal_groups(
        self,
        signals: list[Signal],
        signal_date: date,
        entry_date: date,
        price_preference: str,
    ) -> None:
        grouped: dict[str, list[Signal]] = {}
        for sig in signals:
            key = sig.group_id or f"{sig.underlying}:{sig.option_type}:{sig.direction}:{signal_date.isoformat()}:{uuid.uuid4().hex[:6]}"
            grouped.setdefault(key, []).append(sig)

        for group_id, group_signals in grouped.items():
            allowed, reason = self.rm.can_open_trade()
            if not allowed:
                logger.debug("Entry blocked on %s: %s", entry_date, reason)
                continue
            position = self._build_position(group_id, group_signals, signal_date, entry_date, price_preference)
            if not position.legs:
                continue
            self.open_positions[position.id] = position
            for leg in position.legs:
                self.open_trades[leg.id] = leg
            self.rm.register_open(position.id)

    def _build_position(
        self,
        group_id: str,
        signals: list[Signal],
        signal_date: date,
        entry_date: date,
        price_preference: str,
    ) -> Position:
        first = signals[0]
        structure_type = first.structure_type or first.meta.get("pair_type") or "single"
        legs: list[Trade] = []

        for sig in signals:
            df = self.data.get(sig.underlying)
            pos = self._date_index.get(sig.underlying, {}).get(entry_date)
            if df is None or pos is None:
                continue
            row = df.iloc[pos]
            spot = float(row["Open"] if price_preference == "open" and "Open" in row else row["Close"])
            vix_val = float(row["VIX"]) if "VIX" in row and pd.notna(row["VIX"]) else 15.0
            vix_source = str(row.get("VIX_source", "unknown"))
            sigma = vix_val / 100.0
            expiry = sig.expiry or self._default_expiry(signal_date)
            T = time_to_expiry(entry_date, expiry)
            strike = self._resolve_strike(sig, spot, T, sigma)
            premium_raw, pricing_meta, strike, expiry = self._lookup_or_price(
                sig.underlying, entry_date, expiry, strike, sig.option_type, spot, sigma, T, price_preference
            )
            slippage = premium_raw * settings.SLIPPAGE_PCT / 100.0
            entry_premium = premium_raw + slippage if sig.direction == "long" else premium_raw - slippage
            entry_premium = max(entry_premium, 0.01)
            lot_size = settings.LOT_SIZES.get(sig.underlying, settings.DEFAULT_LOT_SIZE)
            lots = self.rm.position_size(entry_premium, lot_size)
            entry_cost = calculate_transaction_cost(
                entry_premium,
                lot_size,
                lots,
                "buy" if sig.direction == "long" else "sell",
            )
            greeks = bs_greeks(spot, strike, T, settings.RISK_FREE_RATE, sigma, sig.option_type)
            meta = {**sig.meta, **pricing_meta}
            if vix_source in {"fallback", "stale"}:
                meta["vix_warning"] = vix_source

            legs.append(Trade(
                id=str(uuid.uuid4())[:8],
                underlying=sig.underlying,
                option_type=sig.option_type,
                direction=sig.direction,
                strike=strike,
                expiry=expiry,
                signal_date=signal_date,
                entry_date=entry_date,
                group_id=group_id,
                structure_type=structure_type,
                leg_label=str(meta.get("leg", "")),
                entry_premium=entry_premium,
                lots=lots,
                lot_size=lot_size,
                entry_cost=entry_cost,
                entry_spot=spot,
                entry_delta=greeks["delta"],
                entry_iv=vix_val,
                entry_theta=greeks["theta"],
                entry_meta=meta,
                pricing_source=str(pricing_meta.get("price_source", "black_scholes")),
                vix_source=vix_source,
            ))

        return Position(group_id, first.underlying, structure_type, signal_date, entry_date, legs)

    def _resolve_strike(self, sig: Signal, spot: float, T: float, sigma: float) -> float:
        if sig.strike:
            return sig.strike
        target_delta = sig.meta.get("target_delta")
        if target_delta and sig.direction == "short":
            return get_strike_by_delta(
                spot, T, settings.RISK_FREE_RATE, sigma, target_delta, sig.option_type,
                step=self._strike_step(sig.underlying),
            )
        return get_atm_strike(spot, self._strike_step(sig.underlying))

    def _lookup_or_price(
        self,
        underlying: str,
        trade_date: date,
        expiry: date,
        strike: float,
        option_type: str,
        spot: float,
        sigma: float,
        T: float,
        price_preference: str,
    ) -> tuple[float, dict, float, date]:
        lookup = _get_chain_lookup(underlying)
        if lookup and lookup.has_data_for(trade_date):
            real_strike = lookup.nearest_strike(strike)
            real_expiry = lookup.nearest_expiry(trade_date, min_days=0)
            if real_expiry is not None:
                real_expiry_date = real_expiry.date() if hasattr(real_expiry, "date") else real_expiry
                if hasattr(lookup, "premium_with_meta"):
                    real_px, meta = lookup.premium_with_meta(
                        trade_date, real_expiry_date, real_strike, option_type, price_preference
                    )
                else:
                    real_px = lookup.premium(trade_date, real_expiry_date, real_strike, option_type)
                    meta = {"price_source": "bhavcopy_close"}
                if real_px:
                    return real_px, meta, float(real_strike), real_expiry_date
        return (
            bs_price(spot, strike, T, settings.RISK_FREE_RATE, sigma, option_type),
            {"price_source": "black_scholes"},
            strike,
            expiry,
        )

    def _check_position_exits(self, position_id: str, current_date: date) -> None:
        position = self.open_positions.get(position_id)
        if not position:
            return
        if any((leg.expiry - current_date).days <= settings.DAYS_BEFORE_EXPIRY_EXIT for leg in position.legs):
            self._close_position(position_id, current_date, "expiry")
            return

        gross = 0.0
        for leg in position.legs:
            current_premium, _spot = self._current_leg_price(leg, current_date)
            if current_premium is None:
                return
            multiplier = 1 if leg.direction == "long" else -1
            gross += multiplier * (current_premium - leg.entry_premium) * leg.lots * leg.lot_size
        pnl_pct = gross / position.notional_basis * 100

        if position.risk_side == "long":
            if pnl_pct <= -settings.BUY_STOP_LOSS_PCT:
                self._close_position(position_id, current_date, "sl")
            elif pnl_pct >= settings.BUY_TARGET_PCT:
                self._close_position(position_id, current_date, "target")
        else:
            if pnl_pct <= -settings.SELL_STOP_LOSS_PCT:
                self._close_position(position_id, current_date, "sl")
            elif pnl_pct >= settings.SELL_TARGET_PCT:
                self._close_position(position_id, current_date, "target")

    def _handle_exit_signal(self, sig: Signal, current_date: date) -> None:
        for position_id, position in list(self.open_positions.items()):
            if position.underlying != sig.underlying:
                continue
            if sig.group_id and sig.group_id == position.id:
                self._close_position(position_id, current_date, sig.exit_reason or "signal")
                return
            if sig.structure_type != "single" and sig.structure_type == position.structure_type:
                self._close_position(position_id, current_date, sig.exit_reason or "signal")
                return
            if any(leg.option_type == sig.option_type and leg.direction == sig.direction for leg in position.legs):
                self._close_position(position_id, current_date, sig.exit_reason or "signal")
                return

    def _close_position(self, position_id: str, current_date: date, reason: str) -> None:
        position = self.open_positions.pop(position_id, None)
        if not position:
            return
        for leg in position.legs:
            self.open_trades.pop(leg.id, None)
            exit_premium_raw, spot = self._current_leg_price(leg, current_date)
            if exit_premium_raw is None:
                continue
            slippage = exit_premium_raw * settings.SLIPPAGE_PCT / 100.0
            exit_premium = exit_premium_raw - slippage if leg.direction == "long" else exit_premium_raw + slippage
            exit_premium = max(exit_premium, 0.01)
            exit_cost = calculate_transaction_cost(
                exit_premium,
                leg.lot_size,
                leg.lots,
                "sell" if leg.direction == "long" else "buy",
            )
            leg.close(current_date, exit_premium, spot, reason, exit_cost)
            self.closed_trades.append(leg)
            self.strategy.on_trade_closed(leg)
        position.mark_closed(current_date, reason)
        self.closed_positions.append(position)
        self.rm.register_close(position.id, position.net_pnl)

    def _current_leg_price(self, leg: Trade, current_date: date) -> tuple[float | None, float]:
        df = self.data.get(leg.underlying)
        pos = self._date_index.get(leg.underlying, {}).get(current_date)
        if df is None or pos is None:
            return None, 0.0
        row = df.iloc[pos]
        spot = float(row["Close"])
        vix_val = float(row["VIX"]) if "VIX" in row and pd.notna(row["VIX"]) else leg.entry_iv
        px, _, _, _ = self._lookup_or_price(
            leg.underlying,
            current_date,
            leg.expiry,
            leg.strike,
            leg.option_type,
            spot,
            vix_val / 100.0,
            time_to_expiry(current_date, leg.expiry),
            "close",
        )
        return px, spot

    def _get_trading_dates(self) -> list[date]:
        all_dates = set()
        for df in self.data.values():
            dates = set(df.loc[(df.index.date >= self.start) & (df.index.date <= self.end)].index.date)
            all_dates = dates if not all_dates else all_dates & dates
        return sorted(all_dates)

    def _next_trading_date(self, from_date: date) -> date | None:
        for d in getattr(self, "_all_dates", []):
            if d > from_date:
                return d
        return None

    def _default_expiry(self, from_date: date):
        return next_expiry(from_date, weekly=False)

    def _strike_step(self, underlying: str) -> float:
        return settings.STRIKE_STEPS.get(underlying, 50.0)

    def get_equity_curve(self) -> pd.DataFrame:
        return pd.DataFrame(self.equity_curve).set_index("date")
