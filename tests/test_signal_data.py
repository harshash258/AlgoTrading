import io
import zipfile
from datetime import date
from unittest.mock import Mock
import pandas as pd
import pytest
from algo_trading.data import signal_data, bhavcopy
from algo_trading.data.ingestion import parse_exchange_dates
from algo_trading.notifications import telegram
from algo_trading.core import backtester
from tests.test_execution_realism import _market_data

D = date(2026, 9, 4)


def archive_bytes(asof="2026-09-04"):
    rows = [dict(TckrSymb=s, XpryDt="2026-09-29", StrkPric=22000, OptnTp="CE",
                 ClsPric=20, OpnPric=20, OpnIntrst=5000, TtlTradgVol=500, TradDt=asof)
            for s in ("NIFTY", "BANKNIFTY", "FINNIFTY")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("options.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_data, "ROOT", tmp_path)
    monkeypatch.setattr(backtester, "_chain_lookup", None)
    monkeypatch.setattr(backtester, "_chain_lookups", {})
    return tmp_path


def test_udiff_url_and_iso_dates():
    assert bhavcopy._url_for(D).endswith("BhavCopy_NSE_FO_0_0_0_20260904_F_0000.csv.zip")
    dates = parse_exchange_dates(pd.Series(["2026-09-04", "04-SEP-2026"]))
    assert dates.dt.date.tolist() == [D, D]


def test_archive_checks_internal_date():
    assert bhavcopy._valid_archive(archive_bytes(), D)
    assert not bhavcopy._valid_archive(archive_bytes("2026-09-03"), D)
    assert not bhavcopy._valid_archive(b"PKnotazip", D)


def test_alternate_filename_is_loaded_without_download(isolated, monkeypatch):
    (isolated / "fo_bhavcopy_20260904.zip").write_bytes(archive_bytes())
    downloader = Mock()
    monkeypatch.setattr(signal_data, "download_bhavcopy", downloader)
    report = signal_data.prepare_signal_data(D, output_dir=isolated)
    assert all(s["status"] == "ready" for s in report["underlyings"].values())
    assert backtester._get_chain_lookup("^NSEI").has_data_for(D)
    downloader.assert_not_called()


def test_missing_file_has_bounded_retries_and_no_stale_fallback(isolated, monkeypatch):
    (isolated / "fo_bhavcopy_20260903.zip").write_bytes(archive_bytes("2026-09-03"))
    downloader = Mock(return_value=None)
    session = Mock()
    monkeypatch.setattr(signal_data, "_make_session", lambda: session)
    monkeypatch.setattr(signal_data, "download_bhavcopy", downloader)
    monkeypatch.setattr(signal_data.time, "sleep", lambda _: None)
    report = signal_data.prepare_signal_data(D, output_dir=isolated)
    assert downloader.call_count == 3
    assert all(s["status"] != "ready" for s in report["underlyings"].values())
    assert backtester._get_chain_lookup("^NSEI") is None
    session.close.assert_called_once()


def test_invalid_download_does_not_replace_valid_cached_file(isolated):
    path = isolated / "fo04SEP2026bhav.csv.zip"
    valid = archive_bytes()
    path.write_bytes(valid)
    session = Mock()
    session.get.return_value = Mock(status_code=200, content=archive_bytes("2026-09-03"))
    assert bhavcopy.download_bhavcopy(D, session, str(isolated), overwrite=True) is None
    assert path.read_bytes() == valid


def setup_message(monkeypatch, ready=False):
    monkeypatch.setattr(telegram, "india_today", lambda: D)
    monkeypatch.setattr(telegram, "get_combined_dataset", lambda **_: _market_data(dates=[str(D)]))
    monkeypatch.setattr(telegram, "prepare_signal_data", lambda _: {
        "underlyings": {"^NSEI": {"status": "ready" if ready else "same-session bhavcopy unavailable"}}})


def test_blocked_alert_is_not_market_no_signal_or_trade_instruction(monkeypatch):
    setup_message(monkeypatch)
    monkeypatch.setattr(telegram, "_collect_signals", lambda *_: ({}, ["NIFTY: candidates blocked"]))
    text = "\n".join(telegram.generate_signal_messages())
    assert "Option-data validation unavailable" in text
    assert "not a no-signal market assessment" in text
    assert "Execute manually" not in text
    assert "Set GTT" not in text
    assert "No signals today across all strategies" not in text


def test_genuine_no_signal_has_no_data_retry_instruction(monkeypatch):
    setup_message(monkeypatch, ready=True)
    monkeypatch.setattr(telegram, "_collect_signals", lambda *_: ({}, []))
    text = "\n".join(telegram.generate_signal_messages())
    assert "No strategy entry signals were generated" in text
    assert "Retry" not in text
    assert "Execute manually" not in text


def test_stale_candles_are_not_stamped_today(monkeypatch):
    from tests.test_execution_realism import OneShotStrategy
    from algo_trading.strategies.base import Signal
    monkeypatch.setattr(telegram, "_all_strategies", lambda: [("test", OneShotStrategy([Signal(D, "^NSEI", "long", "CE")]))])
    collected, reasons = telegram._collect_signals(_market_data(dates=["2026-09-03"]), D)
    assert not collected
    assert "spot candle unavailable" in reasons[0]
