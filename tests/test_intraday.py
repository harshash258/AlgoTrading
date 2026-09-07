from datetime import date
import pandas as pd
import pytest
from algo_trading.data.intraday import session_signals, load_intraday_csv
from algo_trading.core.intraday import IntradayBacktester
from algo_trading.core.contracts import ContractMaster, ContractSpec


def dataset():
    idx = pd.date_range("2026-09-04 09:20", periods=7, freq="5min", tz="Asia/Kolkata")
    close = [22000, 22000, 22000, 22050, 22060, 22070, 22080]
    bars = pd.DataFrame({"Open": close, "High": [v+10 for v in close], "Low": [v-10 for v in close],
                         "Close": close, "Volume": 1000}, index=idx)
    quotes = pd.DataFrame({"underlying": "^NSEI", "expiry": date(2026, 9, 8), "strike": 22050,
                           "option_type": "CE", "bid": 19.0, "ask": 20.0, "bid_size": 10000,
                           "ask_size": 10000}, index=idx)
    master = ContractMaster([ContractSpec("^NSEI", date(2026, 9, 8), 65, date(2026, 9, 1), date(2026, 9, 8))])
    return bars, quotes, master


def test_orb_freezes_opening_range_and_waits_for_next_observation():
    bars, quotes, master = dataset()
    signal = session_signals(bars)
    assert signal.iloc[:3].eq(0).all()
    assert signal.iloc[3] == 1
    bt = IntradayBacktester(bars, quotes, "^NSEI", master)
    trades = bt.run()
    assert len(trades) == 1
    assert trades[0].entry_date == bars.index[4]
    assert trades[0].entry_premium == 20
    assert trades[0].exit_premium == 19
    assert trades[0].exit_reason == "session_end"
    assert bt.get_equity_curve().iloc[-1].capital == pytest.approx(500000 + trades[0].net_pnl)


def test_stale_quotes_cannot_fill():
    bars, quotes, master = dataset()
    quotes.index = quotes.index - pd.Timedelta(minutes=1)
    assert IntradayBacktester(bars, quotes, "^NSEI", master).run() == []


def test_insufficient_exit_depth_is_unresolved():
    bars, quotes, master = dataset()
    quotes.loc[quotes.index[-1], "bid_size"] = 0
    with pytest.raises(ValueError, match="exit depth"):
        IntradayBacktester(bars, quotes, "^NSEI", master).run()


def test_vwap_resets_at_session_start():
    bars, _, _ = dataset()
    second = bars.copy()
    second.index += pd.Timedelta(days=3)
    second[["Open", "High", "Low", "Close"]] += 2000
    combined = pd.concat([bars, second])
    pd.testing.assert_series_equal(session_signals(combined, "vwap").loc[second.index],
                                   session_signals(second, "vwap"), check_freq=False)


def test_naive_timestamps_rejected(tmp_path):
    path = tmp_path / "bars.csv"
    pd.DataFrame({"timestamp": ["2026-09-04 09:20"], "Open": [1], "High": [1], "Low": [1],
                  "Close": [1], "Volume": [1]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="timezone"):
        load_intraday_csv(path)
