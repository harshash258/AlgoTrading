import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import config
import algo_trading.core.backtester as backtester_module
from algo_trading.core.backtester import Backtester
from algo_trading.strategies.base import BaseStrategy, Signal
from algo_trading.notifications import telegram
from algo_trading.core.regime import classify_regime, summarize_regime_performance


def _market_data(tickers=("^NSEI",), dates=None, vix_source="live"):
    idx = pd.to_datetime(dates if dates is not None else ["2026-09-04", "2026-09-07", "2026-09-08"])
    out = {}
    for ticker in tickers:
        base = 48000 if ticker == "^NSEBANK" else 22000
        if ticker == "NIFTY_FIN_SERVICE.NS":
            base = 21000
        out[ticker] = pd.DataFrame({
            "Open": [base + 10 * i for i in range(len(idx))],
            "High": [base + 100 + 10 * i for i in range(len(idx))],
            "Low": [base - 100 + 10 * i for i in range(len(idx))],
            "Close": [base + 50 + 10 * i for i in range(len(idx))],
            "Volume": [100000] * len(idx),
            "VIX": [15.0] * len(idx),
            "VIX_source": [vix_source] * len(idx),
        }, index=idx)
    return out


class OneShotStrategy(BaseStrategy):
    def __init__(self, signals):
        self._signals = signals
        self._sent = set()

    @property
    def name(self):
        return "one_shot"

    def generate_signals(self, data, vix, current_date):
        ticker = data.attrs["ticker"]
        if (ticker, current_date) in self._sent:
            return []
        self._sent.add((ticker, current_date))
        return [s for s in self._signals if s.underlying == ticker and s.date == current_date]


class FakeLookup:
    def __init__(self, symbol, premiums=None, strikes=None, expiries=None, oi=5000, volume=500):
        self.symbol = symbol
        self.premiums = premiums or {}
        self.strikes = strikes or [22000.0, 22100.0, 48000.0, 21000.0]
        self.expiries = [pd.Timestamp(e) for e in (expiries or [date(2026, 9, 29)])]
        self.oi = oi
        self.volume = volume

    def has_data_for(self, trade_date):
        return True

    def nearest_strike(self, strike):
        return min(self.strikes, key=lambda item: abs(item - strike))

    def nearest_expiry(self, trade_date, min_days=0):
        return self.expiries[0]

    def has_expiry(self, expiry):
        return pd.Timestamp(expiry) in self.expiries

    def premium_with_meta(self, trade_date, expiry, strike, opt_type, price_preference="close"):
        key = (self.symbol, pd.Timestamp(trade_date).date(), float(strike), opt_type)
        val = self.premiums.get(key, self.premiums.get((self.symbol, float(strike), opt_type), 20.0))
        return val, {"price_source": f"{self.symbol}_{price_preference}", "oi": self.oi, "volume": self.volume}

    def premium(self, trade_date, expiry, strike, opt_type):
        return self.premium_with_meta(trade_date, expiry, strike, opt_type)[0]


class BacktestRealismTests(unittest.TestCase):
    def setUp(self):
        self.prev_lookup = backtester_module._chain_lookup
        self.prev_lookups = dict(backtester_module._chain_lookups)
        self.prev_bhavcopy_folder = backtester_module.settings.BHAVCOPY_FOLDER
        backtester_module._chain_lookup = None
        backtester_module._chain_lookups = {}
        backtester_module.settings.BHAVCOPY_FOLDER = None

    def tearDown(self):
        backtester_module._chain_lookup = self.prev_lookup
        backtester_module._chain_lookups = self.prev_lookups
        backtester_module.settings.BHAVCOPY_FOLDER = self.prev_bhavcopy_folder

    def test_friday_signal_enters_next_trading_day_open(self):
        sig = Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="CE")
        bt = Backtester(
            OneShotStrategy([sig]),
            _market_data(),
            start=date(2026, 9, 4),
            end=date(2026, 9, 8),
        )

        trades = bt.run()

        self.assertEqual(trades[0].signal_date, date(2026, 9, 4))
        self.assertEqual(trades[0].entry_date, date(2026, 9, 7))
        self.assertNotEqual(trades[0].entry_date, trades[0].signal_date)

    def test_banknifty_and_finnifty_use_their_own_chain_lookup(self):
        expiry = date(2026, 9, 29)
        backtester_module._chain_lookups = {
            "NIFTY": FakeLookup("NIFTY", {("NIFTY", 48000.0, "CE"): 999.0}),
            "BANKNIFTY": FakeLookup("BANKNIFTY", {("BANKNIFTY", 48000.0, "CE"): 41.0}),
            "FINNIFTY": FakeLookup("FINNIFTY", {("FINNIFTY", 21000.0, "PE"): 31.0}),
        }
        signals = [
            Signal(date=date(2026, 9, 4), underlying="^NSEBANK", direction="long", option_type="CE", strike=48000, expiry=expiry),
            Signal(date=date(2026, 9, 4), underlying="NIFTY_FIN_SERVICE.NS", direction="long", option_type="PE", strike=21000, expiry=expiry),
        ]
        bt = Backtester(
            OneShotStrategy(signals),
            _market_data(("^NSEBANK", "NIFTY_FIN_SERVICE.NS")),
            date(2026, 9, 4),
            date(2026, 9, 8),
        )

        trades = bt.run()
        by_underlying = {trade.underlying: trade for trade in trades}

        self.assertIn("BANKNIFTY_open", by_underlying["^NSEBANK"].pricing_source)
        self.assertIn("FINNIFTY_open", by_underlying["NIFTY_FIN_SERVICE.NS"].pricing_source)
        self.assertLess(by_underlying["^NSEBANK"].entry_premium, 100)

    def test_multi_leg_structure_counts_as_one_open_position(self):
        expiry = date(2026, 9, 29)
        signals = [
            Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="CE", strike=22000, expiry=expiry, group_id="g1", structure_type="straddle"),
            Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="PE", strike=22000, expiry=expiry, group_id="g1", structure_type="straddle"),
            Signal(date=date(2026, 9, 4), underlying="^NSEBANK", direction="long", option_type="CE", strike=48000, expiry=expiry, group_id="g2", structure_type="straddle"),
            Signal(date=date(2026, 9, 4), underlying="^NSEBANK", direction="long", option_type="PE", strike=48000, expiry=expiry, group_id="g2", structure_type="straddle"),
        ]
        with patch.object(config, "MAX_OPEN_POSITIONS", 1), patch.object(backtester_module.settings, "MAX_OPEN_POSITIONS", 1):
            bt = Backtester(
                OneShotStrategy(signals),
                _market_data(("^NSEI", "^NSEBANK")),
                date(2026, 9, 4),
                date(2026, 9, 8),
                execution_timing="same_day_close",
            )
            bt.run()

        self.assertEqual(len(bt.closed_positions), 1)
        self.assertEqual(len(bt.closed_positions[0].legs), 2)

    def test_grouped_straddle_exits_on_structure_target(self):
        expiry = date(2026, 9, 29)
        premiums = {
            ("NIFTY", date(2026, 9, 4), 22000.0, "CE"): 10.0,
            ("NIFTY", date(2026, 9, 4), 22000.0, "PE"): 10.0,
            ("NIFTY", date(2026, 9, 7), 22000.0, "CE"): 50.0,
            ("NIFTY", date(2026, 9, 7), 22000.0, "PE"): 50.0,
        }
        backtester_module._chain_lookups = {"NIFTY": FakeLookup("NIFTY", premiums)}
        signals = [
            Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="CE", strike=22000, expiry=expiry, group_id="str", structure_type="straddle"),
            Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="PE", strike=22000, expiry=expiry, group_id="str", structure_type="straddle"),
        ]
        bt = Backtester(
            OneShotStrategy(signals),
            _market_data(),
            date(2026, 9, 4),
            date(2026, 9, 8),
            execution_timing="same_day_close",
        )

        bt.run()

        self.assertEqual(bt.closed_positions[0].exit_reason, "target")
        self.assertEqual(len(bt.closed_positions[0].legs), 2)

    def test_fallback_vix_is_tagged_in_backtest(self):
        sig = Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="CE")
        bt = Backtester(
            OneShotStrategy([sig]),
            _market_data(vix_source="fallback"),
            date(2026, 9, 4),
            date(2026, 9, 8),
            execution_timing="same_day_close",
        )

        trades = bt.run()

        self.assertEqual(trades[0].vix_source, "fallback")
        self.assertEqual(trades[0].entry_meta["vix_warning"], "fallback")


class TelegramValidationTests(unittest.TestCase):
    def test_volatility_strategy_suppressed_on_fallback_vix(self):
        sig = Signal(date=date(2026, 9, 4), underlying="^NSEI", direction="long", option_type="CE")
        with patch.object(telegram, "_all_strategies", return_value=[("Long Straddle (Low IV)", OneShotStrategy([sig]))]):
            collected, suppressed = telegram._collect_signals(_market_data(vix_source="fallback"), date(2026, 9, 4))

        self.assertEqual(collected, {})
        self.assertTrue(any("VIX source is fallback" in item for item in suppressed))

    def test_contract_validation_rejects_missing_expiry(self):
        sig = {"strategy_label": "x", "option_type": "CE", "expiry": None, "strike": 22000, "meta": {}}
        with patch.object(telegram, "_get_chain_lookup", return_value=FakeLookup("NIFTY")):
            reason = telegram._validate_contract_signal("^NSEI", sig, 22000, 22000, date(2026, 9, 4))

        self.assertEqual(reason, "missing expiry")

    def test_contract_validation_rejects_missing_strike_distance(self):
        sig = {"strategy_label": "x", "option_type": "CE", "expiry": date(2026, 9, 29), "strike": 23000, "meta": {}}
        lookup = FakeLookup("NIFTY", strikes=[22000.0])
        with patch.object(telegram, "_get_chain_lookup", return_value=lookup):
            reason = telegram._validate_contract_signal("^NSEI", sig, 22000, 22000, date(2026, 9, 4))

        self.assertIn("beyond max distance", reason)

    def test_contract_validation_rejects_zero_premium(self):
        sig = {"strategy_label": "x", "option_type": "CE", "expiry": date(2026, 9, 29), "strike": 22000, "meta": {}}
        lookup = FakeLookup("NIFTY", {("NIFTY", 22000.0, "CE"): 0.0})
        with patch.object(telegram, "_get_chain_lookup", return_value=lookup):
            reason = telegram._validate_contract_signal("^NSEI", sig, 22000, 22000, date(2026, 9, 4))

        self.assertEqual(reason, "premium is zero or unavailable")

    def test_contract_validation_rejects_low_liquidity(self):
        sig = {"strategy_label": "x", "option_type": "CE", "expiry": date(2026, 9, 29), "strike": 22000, "meta": {}}
        with patch.object(telegram, "_get_chain_lookup", return_value=FakeLookup("NIFTY", volume=1, oi=1)):
            reason = telegram._validate_contract_signal("^NSEI", sig, 22000, 22000, date(2026, 9, 4))

        self.assertTrue("below threshold" in reason)

    def test_contract_validation_accepts_valid_contract(self):
        sig = {"strategy_label": "x", "option_type": "CE", "expiry": date(2026, 9, 29), "strike": 22000, "meta": {}}
        with patch.object(telegram, "_get_chain_lookup", return_value=FakeLookup("NIFTY")):
            reason = telegram._validate_contract_signal("^NSEI", sig, 22000, 22000, date(2026, 9, 4))

        self.assertEqual(reason, "")
        self.assertIn("bid/ask unavailable", sig["meta"]["validation"])


class RegimeTests(unittest.TestCase):
    def test_regime_classification_detects_gap_and_trend(self):
        df = _market_data(dates=pd.date_range("2026-01-01", periods=70, freq="B"))["^NSEI"]
        df["Close"] = [float(20000 + x * 80) for x in range(70)]
        df["Open"] = df["Open"].astype(float)
        df.iloc[-1, df.columns.get_loc("Open")] = float(df["Close"].iloc[-2]) * 1.01

        regime = classify_regime(df)

        self.assertEqual(regime.primary, "trend")
        self.assertIn("gap", regime.tags)

    def test_regime_summary_marks_insufficient_sample(self):
        df = pd.DataFrame({
            "Regime": ["trend", "trend"],
            "Strategy": ["A", "A"],
            "net_pnl": [10.0, -2.0],
        })

        summary = summarize_regime_performance(df, min_trades=5)

        self.assertEqual(summary.iloc[0]["Sample"], "insufficient")


if __name__ == "__main__":
    unittest.main()
