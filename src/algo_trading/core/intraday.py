"""Quote-driven intraday long-option replay for ORB and session VWAP.

Signals execute at the following bar timestamp against a fresh observed ask.
Exits use bid and available size; no fills are inferred between observations.
This models one underlying and one open position, with no overnight carry.
"""
import uuid
from datetime import time
import pandas as pd
from config import settings
from algo_trading.core.backtester import Trade
from algo_trading.core.pricing import calculate_transaction_cost
from algo_trading.core.risk_manager import RiskManager
from algo_trading.data.intraday import session_signals


class IntradayBacktester:
    def __init__(self, bars, quotes, underlying, contract_master, strategy="orb",
                 starting_capital=500000, max_quote_age_seconds=30, max_spread_pct=10,
                 allow_expiry_day=False, expiry_day_cutoff="12:00", min_hours_to_expiry=2):
        if bars.empty or bars.index.tz is None or quotes.index.tz is None:
            raise ValueError("Nonempty timezone-aware bars and quotes required")
        self.bars, self.quotes, self.underlying = bars, quotes, underlying
        self.master, self.strategy = contract_master, strategy
        self.starting_capital = starting_capital
        self.max_age, self.max_spread = max_quote_age_seconds, max_spread_pct
        self.allow_expiry_day = allow_expiry_day
        self.expiry_day_cutoff = time.fromisoformat(expiry_day_cutoff)
        self.min_hours_to_expiry = min_hours_to_expiry
        self.trades, self.equity_curve, self.rejections = [], [], []

    def _quote(self, timestamp, expiry, strike, kind):
        q = self.quotes
        found = q.loc[(q.index <= timestamp) & (q.index >= timestamp - pd.Timedelta(seconds=self.max_age)) &
                      (q.underlying == self.underlying) & (q.expiry == expiry) &
                      (q.strike == strike) & (q.option_type == kind)]
        if found.empty:
            return None
        row = found.iloc[-1]
        if row.ask <= 0 or row.bid < 0 or row.ask < row.bid:
            return None
        return row

    def run(self):
        self.trades, self.equity_curve, self.rejections = [], [], []
        rm = RiskManager(self.starting_capital)
        bars = self.bars.between_time("09:15", "15:30", inclusive="right")
        signals = session_signals(bars, self.strategy)
        position, pending, session = None, None, None
        for i, (ts, bar) in enumerate(bars.iterrows()):
            if ts.date() != session:
                session = ts.date()
                rm.day_start_equity = rm.equity
                pending = None
            last_bar = i == len(bars) - 1 or bars.index[i + 1].date() != ts.date()
            if pending and position is None and ts.time() < time(15, 15) and not last_bar:
                kind, expiry, strike, signal_ts = pending
                quote = self._quote(ts, expiry, strike, kind)
                spec = self.master.resolve(self.underlying, expiry, ts.date())
                allowed, reason = rm.can_open_trade()
                from algo_trading.core.volatility import expiry_days
                expiry_allowed = (expiry_days(expiry, ts) * 24 >= self.min_hours_to_expiry and
                    (expiry != ts.date() or (self.allow_expiry_day and ts.time() < self.expiry_day_cutoff)))
                if quote is not None and spec and spec.settlement == "cash" and allowed and expiry_allowed:
                    spread_pct = (quote.ask - quote.bid) / quote.ask * 100
                    fee = calculate_transaction_cost(quote.ask, spec.lot_size, 1, "buy", ts.date())
                    loss = quote.ask * spec.lot_size + fee
                    lots = min(rm.structure_size(loss, loss), int(quote.ask_size // spec.lot_size))
                    if lots > 0 and spread_pct <= self.max_spread:
                        position = Trade(str(uuid.uuid4()), self.underlying, kind, "long", strike, expiry,
                                         ts, signal_date=signal_ts, entry_premium=float(quote.ask),
                                         lot_size=spec.lot_size, lots=lots, entry_spot=float(bar.Close),
                                         pricing_source="observed_ask", entry_meta={"strategy": self.strategy})
                        position.entry_cost = calculate_transaction_cost(quote.ask, spec.lot_size, lots, "buy", ts.date())
                        rm.register_open(position.id, loss * lots, loss * lots)
                if position is None:
                    self.rejections.append({"timestamp": ts, "reason": reason if not allowed else "missing specification/quote, spread, depth or risk limit"})
                pending = None
            if position is not None:
                quote = self._quote(ts, position.expiry, position.strike, position.option_type)
                if quote is None:
                    raise ValueError(f"Missing fresh quote for held contract at {ts}")
                qty = position.lots * position.lot_size
                fee = calculate_transaction_cost(quote.bid, position.lot_size, position.lots, "sell", ts.date())
                pnl = (quote.bid - position.entry_premium) * qty - position.entry_cost - fee
                rm.mark_to_market(pnl)
                pct = pnl / (position.entry_premium * qty) * 100
                reason = ""
                if ts.time() >= time(15, 20) or last_bar:
                    reason = "session_end"
                elif pct <= -settings.BUY_STOP_LOSS_PCT:
                    reason = "sl"
                elif pct >= settings.BUY_TARGET_PCT:
                    reason = "target"
                elif rm.halted:
                    reason = "portfolio_halt"
                if reason:
                    if quote.bid_size < qty:
                        raise ValueError(f"Insufficient displayed exit depth at {ts}; position remains unresolved")
                    position.close(ts, float(quote.bid), float(bar.Close), reason, fee)
                    position.exit_pricing_source = "observed_bid"
                    self.trades.append(position)
                    rm.register_close(position.id, position.net_pnl)
                    position = None
                    rm.mark_to_market(0)
            self.equity_curve.append({"date": ts, "capital": rm.equity})
            if position is None and signals.loc[ts] and ts.time() < time(15, 10) and not last_bar:
                kind = "CE" if signals.loc[ts] > 0 else "PE"
                expiries = self.master.expiries(self.underlying, ts.date(), min_days=0 if self.allow_expiry_day else 1)
                if expiries:
                    # Contract selection only sees quotes observed at signal time.
                    q = self.quotes
                    available = q.loc[(q.index <= ts) & (q.index >= ts - pd.Timedelta(seconds=self.max_age)) &
                                      (q.underlying == self.underlying) & (q.expiry == expiries[0]) & (q.option_type == kind)]
                    if not available.empty:
                        strike = min(available.strike.unique(), key=lambda k: abs(k - bar.Close))
                        pending = kind, expiries[0], float(strike), ts
        return self.trades

    def get_equity_curve(self):
        result = pd.DataFrame(self.equity_curve, columns=["date", "capital"]).set_index("date")
        # Portfolio ratios are calculated from daily returns, not per-minute returns.
        result = result.resample("D").last().dropna()
        result.attrs["starting_capital"] = self.starting_capital
        result.attrs["pricing_mode"] = "intraday_quotes"
        result.attrs["rejections"] = self.rejections
        return result
