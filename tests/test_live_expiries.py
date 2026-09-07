from datetime import date

import pandas as pd

from algo_trading.data.ingestion import ChainLookup
from algo_trading.notifications import telegram
from algo_trading.strategies.base import BaseStrategy, Signal
from tests.test_execution_realism import _market_data


D = date(2026, 9, 7)


def lookup(expiries):
    return ChainLookup(pd.DataFrame([
        dict(date=pd.Timestamp(D), expiry=pd.Timestamp(e), strike=23800,
             opt_type="CE", close=50, open=50, oi=5000, volume=500)
        for e in expiries
    ]))


class ListedExpiryStrategy(BaseStrategy):
    name = "test"

    def generate_signals(self, data, vix, current_date):
        return [Signal(current_date, data.attrs["ticker"], "long", "CE",
                       expiry=self.resolve_expiry(data, current_date, weekly=True))]


def test_collector_selects_expiry_per_underlying(monkeypatch):
    monthly = date(2026, 9, 29)
    weekly = date(2026, 9, 8)
    chains = {"^NSEI": lookup([D, weekly, monthly]),
              "^NSEBANK": lookup([monthly]), "FINNIFTY.NS": lookup([monthly])}
    monkeypatch.setattr(telegram, "_get_chain_lookup", chains.get)
    monkeypatch.setattr(telegram, "_all_strategies", lambda: [("test", ListedExpiryStrategy())])
    frame = _market_data(dates=[str(D)])["^NSEI"]
    collected, suppressed = telegram._collect_signals({k: frame.copy() for k in chains}, D)
    assert not suppressed
    assert collected["^NSEI"]["sigs"][0]["expiry"] == weekly
    assert collected["^NSEBANK"]["sigs"][0]["expiry"] == monthly
    assert collected["FINNIFTY.NS"]["sigs"][0]["expiry"] == monthly


def test_no_current_chain_does_not_fall_back_to_calendar(monkeypatch):
    chain = lookup([date(2026, 9, 29)])
    assert chain.listed_expiries(date(2026, 9, 8)) == []
    monkeypatch.setattr(telegram, "_get_chain_lookup", lambda _: None)
    monkeypatch.setattr(telegram, "_all_strategies", lambda: [("test", ListedExpiryStrategy())])
    collected, suppressed = telegram._collect_signals(_market_data(dates=[str(D)]), D)
    assert not collected
    assert "no current listed expiries" in suppressed[0]
