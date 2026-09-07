"""Core backtesting engine for NSE option strategies."""

import logging
import os
import uuid
import math
from copy import deepcopy
from collections import Counter
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
    implied_volatility,
)
from algo_trading.core.contracts import ContractMaster
from algo_trading.core.structures import validate_structure, expiry_risk
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

        chain = load_bhavcopy_folder(folder, symbol=nse_symbol, pattern="*.zip")
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
    entry_gamma: float = 0.0
    entry_vega: float = 0.0
    entry_meta: dict = field(default_factory=dict)
    pricing_source: str = "black_scholes"
    vix_source: str = "unknown"
    exit_pricing_source: str = ""
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
    max_loss: float = 0.0
    required_capital: float = 0.0

    @property
    def notional_basis(self) -> float:
        return abs(sum((1 if leg.direction == "long" else -1) * leg.entry_premium * leg.lots * leg.lot_size for leg in self.legs)) or 1

    @property
    def risk_side(self) -> str:
        return "short" if sum((1 if l.direction == "long" else -1) * l.entry_premium * l.lots * l.lot_size for l in self.legs) < 0 else "long"

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
        pricing_mode: str | None = None,
        contract_master: ContractMaster | None = None,
        chain_lookups: dict | None = None,
        risk_overrides: dict | None = None,
        margin_provider=None,
    ):
        self.strategy = strategy
        self._initial_strategy = deepcopy(strategy)
        self.data = data
        self.start = start
        self.end = end
        self.execution_timing = execution_timing or getattr(settings, "EXECUTION_TIMING", "next_open")
        self.pricing_mode = pricing_mode or settings.PRICING_MODE
        if self.pricing_mode not in {"research", "strict"} or self.execution_timing not in {"next_open", "same_day_close"}:
            raise ValueError("Invalid pricing mode or execution timing")
        self.contract_master = contract_master or (ContractMaster.from_csv(settings.CONTRACT_MASTER_PATH)
                                                   if settings.CONTRACT_MASTER_PATH else ContractMaster())
        self.chain_lookups = chain_lookups
        self.risk_overrides = risk_overrides or {}
        self.margin_provider = margin_provider
        if self.pricing_mode == "strict" and self.execution_timing == "same_day_close":
            raise ValueError("Strict mode requires next-open execution")
        self.pricing_counts = Counter()
        self.rejections = []
        self._quote_cache = {}
        self.rm = RiskManager(starting_capital)
        self.open_trades: dict[str, Trade] = {}
        self.open_positions: dict[str, Position] = {}
        self.closed_trades: list[Trade] = []
        self.closed_positions: list[Position] = []
        self.pending_entries: dict[date, list[Signal]] = {}
        self.equity_curve: list[dict] = []

    def run(self) -> list[Trade]:
        self.strategy = deepcopy(self._initial_strategy)
        self.rm.reset()
        self.open_trades = {}
        self.open_positions = {}
        self.closed_trades = []
        self.closed_positions = []
        self.pending_entries = {}
        self.pending_exits = {}
        self.equity_curve = []
        self.pricing_counts.clear()
        self.rejections = []
        self._quote_cache.clear()
        self.portfolio_greeks = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
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
        if self.open_positions:
            raise ValueError("Cannot finalize backtest: held contracts have missing closing quotes")
        self._mark_portfolio(all_dates[-1])
        self.equity_curve[-1] = self._equity_row(all_dates[-1])
        return self.closed_trades

    def _process_day(self, current_date: date) -> None:
        self.rm.day_start_equity = self.rm.equity
        for position_id, reason in self.pending_exits.pop(current_date, []):
            self._close_position(position_id, current_date, reason, "open")
        self._mark_portfolio(current_date, "open")
        self._execute_pending_entries(current_date)
        for position_id in list(self.open_positions):
            self._check_position_exits(position_id, current_date)

        self._mark_portfolio(current_date)

        for ticker, df in self.data.items():
            pos = self._date_index.get(ticker, {}).get(current_date)
            if pos is None:
                continue
            slice_df = df.iloc[: pos + 1].copy()
            slice_df.attrs["ticker"] = ticker
            slice_df.attrs["contract_expiries"] = self.contract_master.expiries(ticker, current_date)
            vix = slice_df.get("VIX", pd.Series(dtype=float))
            entry_signals = []
            for sig in self.strategy.generate_signals(slice_df, vix, current_date):
                if sig.signal_type == "exit":
                    self._handle_exit_signal(sig, current_date)
                else:
                    entry_signals.append(sig)
            self._handle_entry_signals(entry_signals, current_date)

        self._mark_portfolio(current_date)
        self.equity_curve.append(self._equity_row(current_date))

    def _handle_entry_signals(self, signals: list[Signal], signal_date: date) -> None:
        if not signals:
            return
        timings = {s.execution_timing or self.execution_timing for s in signals}
        if len(timings) > 1:
            for timing in timings:
                self._handle_entry_signals([s for s in signals if (s.execution_timing or self.execution_timing) == timing], signal_date)
            return
        timing = timings.pop()
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
                self._reject(group_signals, entry_date, reason)
                continue
            if group_id in self.open_positions:
                continue
            try:
                position = self._build_position(group_id, group_signals, signal_date, entry_date, price_preference)
            except ValueError as exc:
                self._reject(group_signals, entry_date, str(exc))
                continue
            self.open_positions[position.id] = position
            for leg in position.legs:
                self.open_trades[leg.id] = leg
            self.rm.register_open(position.id, position.required_capital, position.max_loss)
            if not hasattr(self, "portfolio_greeks"):
                self.portfolio_greeks = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
            for leg in position.legs:
                for greek in self.portfolio_greeks:
                    self.portfolio_greeks[greek] += (1 if leg.direction == "long" else -1) * leg.lots * leg.lot_size * getattr(leg, f"entry_{greek}")

    def _reject(self, signals, when, reason):
        self.rejections.append({"date": when, "group_id": signals[0].group_id, "reason": reason})
        for sig in signals:
            self.strategy.on_entry_rejected(sig)

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
        if any(sig.structure_type != first.structure_type for sig in signals):
            raise ValueError("Conflicting structure types in signal group")

        for sig in signals:
            df = self.data.get(sig.underlying)
            pos = self._date_index.get(sig.underlying, {}).get(entry_date)
            if df is None or pos is None:
                raise ValueError("Missing underlying bar for a required leg")
            row = df.iloc[pos]
            spot = float(row["Open"] if price_preference == "open" and "Open" in row else row["Close"])
            vol_row = df.iloc[pos - 1] if price_preference == "open" and pos > 0 else row
            if price_preference == "open" and pos == 0:
                raise ValueError("No prior volatility observation for next-open entry")
            vix_val = float(vol_row["VIX"]) if "VIX" in vol_row and pd.notna(vol_row["VIX"]) else 15.0
            vix_source = str(vol_row.get("VIX_source", "unknown"))
            sigma = vix_val / 100.0
            expiry = sig.expiry
            if expiry is None:
                expiries = self.contract_master.expiries(sig.underlying, entry_date)
                expiry = expiries[0] if expiries else self._default_expiry(signal_date)
            if (expiry - entry_date).days <= settings.DAYS_BEFORE_EXPIRY_EXIT:
                raise ValueError("Contract too close to expiry at entry")
            T = time_to_expiry(entry_date, expiry)
            strike = self._resolve_strike(sig, spot, T, sigma)
            premium_raw, pricing_meta, strike, expiry = self._lookup_or_price(
                sig.underlying, entry_date, expiry, strike, sig.option_type, spot, sigma, T, price_preference
            )
            if premium_raw is None or not math.isfinite(premium_raw) or premium_raw <= 0:
                raise ValueError("Missing exact contract entry quote")
            if self.pricing_mode == "strict":
                # End-of-day volume/OI from the entry date are not known at its open.
                liquidity_meta = pricing_meta
                if price_preference == "open":
                    lookup = self.chain_lookups.get(sig.underlying) if self.chain_lookups is not None else _get_chain_lookup(sig.underlying)
                    prior_day = df.index[pos - 1].date()
                    _, liquidity_meta = lookup.premium_with_meta(prior_day, expiry, strike, sig.option_type, "close")
                for field, thresholds in (("volume", settings.MIN_OPTION_VOLUME), ("oi", settings.MIN_OPTION_OI)):
                    val = liquidity_meta.get(field)
                    if val is None or not math.isfinite(float(val)) or val < thresholds.get(sig.underlying, 0):
                        raise ValueError(f"Missing or insufficient {field}")
            slippage = premium_raw * settings.SLIPPAGE_PCT / 100.0
            entry_premium = premium_raw + slippage if sig.direction == "long" else premium_raw - slippage
            entry_premium = max(entry_premium, 0.01)
            spec = self.contract_master.resolve(sig.underlying, expiry, entry_date)
            if self.pricing_mode == "strict" and spec is None:
                raise ValueError("Missing point-in-time contract specification")
            if spec and spec.settlement != "cash":
                raise ValueError("Physical settlement is not supported")
            lot_size = spec.lot_size if spec else settings.LOT_SIZES.get(sig.underlying, settings.DEFAULT_LOT_SIZE)
            lots = 1
            entry_cost = calculate_transaction_cost(
                entry_premium,
                lot_size,
                lots,
                "buy" if sig.direction == "long" else "sell", entry_date,
            )
            contract_iv = implied_volatility(premium_raw, spot, strike, T, settings.RISK_FREE_RATE, sig.option_type)
            greek_sigma = contract_iv if contract_iv is not None else sigma
            greeks = bs_greeks(spot, strike, T, settings.RISK_FREE_RATE, greek_sigma, sig.option_type)
            meta = {**sig.meta, **pricing_meta}
            meta["fee_source"] = "historical_schedule" if settings.FEE_SCHEDULE_PATH else "current_rates_proxy"
            meta["contract_spec_source"] = "master" if spec else "current_settings_proxy"
            meta["iv_source"] = "contract" if contract_iv is not None else "vix_proxy"
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
                entry_iv=greek_sigma * 100,
                entry_theta=greeks["theta"],
                entry_gamma=greeks["gamma"],
                entry_vega=greeks["vega"],
                entry_meta=meta,
                pricing_source=str(pricing_meta.get("price_source", "black_scholes")),
                vix_source=vix_source,
            ))

        validate_structure(legs, structure_type)
        max_loss, debit = expiry_risk(legs)
        uncovered = any(l.direction == "short" for l in legs) and structure_type in {"single", "short_strangle"}
        required = max(max_loss, debit)
        if uncovered:
            if not settings.ALLOW_UNCOVERED_SHORTS or self.margin_provider is None:
                raise ValueError("Uncovered shorts require explicit enablement and a margin/stress provider")
            required, max_loss = self.margin_provider(legs, entry_date)
        lots = self.rm.structure_size(max_loss, required)
        if lots == 0:
            raise ValueError("One structure lot exceeds risk or available capital")
        for leg in legs:
            leg.lots = lots
            leg.entry_meta["structure_max_loss"] = max_loss * lots
            leg.entry_cost = calculate_transaction_cost(leg.entry_premium, leg.lot_size, lots,
                                                        "buy" if leg.direction == "long" else "sell", entry_date)
        # Per-lot fees above are conservative for multi-lot orders.
        for greek, limit in settings.GREEK_LIMITS.items():
            if limit is None:
                continue
            exposure = getattr(self, "portfolio_greeks", {}).get(greek, 0.0)
            exposure += sum((1 if l.direction == "long" else -1) * getattr(l, f"entry_{greek}") * l.lot_size * l.lots for l in legs)
            if abs(exposure) > limit:
                raise ValueError(f"Portfolio {greek} limit exceeded")
        return Position(group_id, first.underlying, structure_type, signal_date, entry_date, legs,
                        max_loss=max_loss * lots, required_capital=required * lots)

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
    ) -> tuple[float | None, dict, float, date]:
        lookup = self.chain_lookups.get(underlying) if self.chain_lookups is not None else _get_chain_lookup(underlying)
        if lookup and lookup.has_data_for(trade_date):
            real_strike = strike
            real_expiry = expiry
            if real_expiry is not None:
                real_expiry_date = real_expiry.date() if hasattr(real_expiry, "date") else real_expiry
                if hasattr(lookup, "premium_with_meta"):
                    real_px, meta = lookup.premium_with_meta(
                        trade_date, real_expiry_date, real_strike, option_type, price_preference
                    )
                elif price_preference == "open":
                    real_px, meta = None, {"price_source": "missing_open"}
                else:
                    real_px = lookup.premium(trade_date, real_expiry_date, real_strike, option_type)
                    meta = {"price_source": "bhavcopy_close"}
                if real_px is not None and math.isfinite(real_px) and real_px >= 0:
                    self.pricing_counts[meta["price_source"]] += 1
                    return real_px, meta, float(real_strike), real_expiry_date
        if self.pricing_mode == "strict":
            self.pricing_counts["missing"] += 1
            return None, {"price_source": "missing"}, strike, expiry
        self.pricing_counts["black_scholes"] += 1
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
            if pnl_pct <= -self.risk_overrides.get("BUY_STOP_LOSS_PCT", settings.BUY_STOP_LOSS_PCT):
                self._close_position(position_id, current_date, "sl")
            elif pnl_pct >= self.risk_overrides.get("BUY_TARGET_PCT", settings.BUY_TARGET_PCT):
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
            source = sig.meta.get("source_strategy")
            if source and position.legs[0].entry_meta.get("source_strategy") != source:
                continue
            if sig.group_id and sig.group_id != position.id:
                continue
            if sig.group_id and sig.group_id == position.id:
                self._schedule_signal_exit(position_id, current_date, sig.exit_reason or "signal")
                return
            if sig.structure_type != "single" and sig.structure_type == position.structure_type:
                self._schedule_signal_exit(position_id, current_date, sig.exit_reason or "signal")
                return
            if any(leg.option_type == sig.option_type and leg.direction == sig.direction for leg in position.legs):
                self._schedule_signal_exit(position_id, current_date, sig.exit_reason or "signal")
                return

    def _schedule_signal_exit(self, position_id, current_date, reason):
        if self.execution_timing == "same_day_close":
            self._close_position(position_id, current_date, reason)
        else:
            next_day = self._next_trading_date(current_date)
            if next_day:
                exits = self.pending_exits.setdefault(next_day, [])
                if not any(pid == position_id for pid, _ in exits):
                    exits.append((position_id, reason))

    def _close_position(self, position_id: str, current_date: date, reason: str, price_preference="close") -> None:
        position = self.open_positions.get(position_id)
        if not position:
            return
        quotes = [self._current_leg_price(leg, current_date, price_preference) for leg in position.legs]
        if any(px is None for px, _ in quotes):
            return
        fills = []
        for leg, (exit_premium_raw, spot) in zip(position.legs, quotes):
            slippage = exit_premium_raw * settings.SLIPPAGE_PCT / 100.0
            exit_premium = max(0.0, exit_premium_raw - slippage if leg.direction == "long" else exit_premium_raw + slippage)
            exit_cost = calculate_transaction_cost(exit_premium, leg.lot_size, leg.lots,
                                                   "sell" if leg.direction == "long" else "buy", current_date)
            fills.append((leg, exit_premium, spot, exit_cost))
        self.open_positions.pop(position_id)
        for leg, exit_premium, spot, exit_cost in fills:
            self.open_trades.pop(leg.id, None)
            leg.close(current_date, exit_premium, spot, reason, exit_cost)
            self.closed_trades.append(leg)
            self.strategy.on_trade_closed(leg)
        position.mark_closed(current_date, reason)
        self.closed_positions.append(position)
        self.rm.register_close(position.id, position.net_pnl)

    def _current_leg_price(self, leg: Trade, current_date: date, price_preference="close") -> tuple[float | None, float]:
        key = (leg.id, current_date, price_preference)
        if key in self._quote_cache:
            return self._quote_cache[key]
        df = self.data.get(leg.underlying)
        pos = self._date_index.get(leg.underlying, {}).get(current_date)
        if df is None or pos is None:
            return None, 0.0
        row = df.iloc[pos]
        spot = float(row["Open"] if price_preference == "open" else row["Close"])
        if price_preference == "open" and pos > 0:
            row = df.iloc[pos - 1]
        vix_val = float(row["VIX"]) if "VIX" in row and pd.notna(row["VIX"]) else leg.entry_iv
        px, meta, _, _ = self._lookup_or_price(
            leg.underlying,
            current_date,
            leg.expiry,
            leg.strike,
            leg.option_type,
            spot,
            vix_val / 100.0,
            time_to_expiry(current_date, leg.expiry),
            price_preference,
        )
        leg.exit_pricing_source = meta["price_source"]
        self._quote_cache[key] = (px, spot)
        return px, spot

    def _mark_portfolio(self, current_date, price_preference="close"):
        unrealized = 0.0
        self.portfolio_greeks = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
        self.stress_loss = 0.0
        stress_pnls = [0.0 for _ in settings.STRESS_SPOT_SHOCKS]
        for leg in self.open_trades.values():
            px, spot = self._current_leg_price(leg, current_date, price_preference)
            if px is None:
                raise ValueError(f"Cannot mark held contract {leg.strike} {leg.expiry}: missing quote")
            side = 1 if leg.direction == "long" else -1
            qty = leg.lots * leg.lot_size
            exit_px = max(0, px * (1 - side * settings.SLIPPAGE_PCT / 100))
            exit_cost = calculate_transaction_cost(exit_px, leg.lot_size, leg.lots, "sell" if side == 1 else "buy", current_date)
            unrealized += side * (exit_px - leg.entry_premium) * qty - leg.entry_cost - exit_cost
            T = time_to_expiry(current_date, leg.expiry)
            sigma = implied_volatility(px, spot, leg.strike, T, settings.RISK_FREE_RATE, leg.option_type)
            sigma = sigma if sigma is not None else leg.entry_iv / 100
            greeks = bs_greeks(spot, leg.strike, T, settings.RISK_FREE_RATE, sigma, leg.option_type)
            for greek in self.portfolio_greeks:
                self.portfolio_greeks[greek] += side * qty * greeks[greek]
            for i, shock in enumerate(settings.STRESS_SPOT_SHOCKS):
                stressed = bs_price(spot * (1 + shock), leg.strike, T, settings.RISK_FREE_RATE,
                                    sigma + settings.STRESS_VOL_SHOCK, leg.option_type)
                stress_pnls[i] += side * qty * (stressed - px)
        self.stress_loss = max(0, -min(stress_pnls, default=0))
        self.rm.mark_to_market(unrealized)
        for greek, limit in settings.GREEK_LIMITS.items():
            if limit is not None and abs(self.portfolio_greeks[greek]) > limit:
                self.rm.halted = True

    def _equity_row(self, when):
        reserved = sum(v[0] for v in self.rm.reservations.values())
        return {"date": when, "capital": self.rm.equity, "realized_capital": self.rm.capital,
                "unrealized_pnl": self.rm.equity - self.rm.capital, "reserved_capital": reserved,
                "margin_utilization_pct": reserved / self.rm.equity * 100 if self.rm.equity > 0 else 0,
                "stress_loss": self.stress_loss, **self.portfolio_greeks}

    def _get_trading_dates(self) -> list[date]:
        all_dates = set()
        for df in self.data.values():
            dates = set(df.loc[(df.index.date >= self.start) & (df.index.date <= self.end)].index.date)
            all_dates.update(dates)
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
        result = pd.DataFrame(self.equity_curve, columns=["date", "capital"] if not self.equity_curve else None).set_index("date")
        result.attrs["starting_capital"] = self.rm.starting_capital
        result.attrs["pricing_counts"] = dict(self.pricing_counts)
        result.attrs["pricing_mode"] = self.pricing_mode
        result.attrs["rejections"] = self.rejections
        return result
