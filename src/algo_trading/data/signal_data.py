"""Prepare a same-session F&O snapshot before producing trade alerts.

Only exact-date rows are accepted. Download failures never relax validation.
The standalone CLI fetches data and writes diagnostics; it does not send messages.
"""
import argparse
import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from config import settings
from algo_trading.data.bhavcopy import download_bhavcopy, _make_session, _fmt_date
from algo_trading.data.ingestion import load_bhavcopy, ChainLookup

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


def india_today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def snapshot_paths(folder, asof):
    return [folder / name for name in (
        f"fo{_fmt_date(asof)}bhav.csv.zip",
        f"fo_bhavcopy_{asof:%Y%m%d}.zip",
        f"BhavCopy_NSE_FO_0_0_0_{asof:%Y%m%d}_F_0000.csv.zip",
    ) if (folder / name).is_file()]


def prepare_signal_data(asof=None, download=True, attempts=3, output_dir=None):
    asof = asof or india_today()
    configured = output_dir or os.environ.get("SIGNAL_BHAVCOPY_FOLDER") or settings.BHAVCOPY_FOLDER
    folder = Path(configured) if configured else None
    if folder is not None and not folder.is_absolute():
        folder = ROOT / folder
    symbols = {ticker: settings.BHAVCOPY_SYMBOLS.get(ticker) for ticker in settings.UNDERLYINGS}
    lookups, status = {}, {}

    def inspect():
        paths = snapshot_paths(folder, asof) if folder else []
        for ticker, symbol in symbols.items():
            lookups[ticker] = None
            reason = "same-session bhavcopy unavailable"
            if not symbol:
                reason = "no configured NSE option symbol"
            for path in paths if symbol else []:
                try:
                    rows = load_bhavcopy(str(path), symbol)
                    exact = rows.loc[rows["date"].dt.date == asof]
                    if not exact.empty:
                        lookups[ticker] = ChainLookup(exact)
                        reason = "ready"
                        break
                    reason = f"archive has no {symbol} option rows for {asof}"
                except Exception as exc:
                    reason = f"invalid archive: {type(exc).__name__}"
            status[ticker] = {"status": reason, "asof": asof.isoformat()}

    inspect()
    if folder and download and asof.weekday() < 5 and any(v is None for v in lookups.values()):
        session = _make_session()
        try:
            for attempt in range(max(0, min(attempts, 3))):
                path = download_bhavcopy(asof, session, str(folder), overwrite=True)
                if path:
                    inspect()
                    if all(v is not None for v in lookups.values()):
                        break
                if attempt + 1 < min(attempts, 3):
                    time.sleep(5)
        finally:
            session.close()
    # Install only this session's lookups. Missing symbols do not fall back to
    # a large historical archive or a synthetic premium during alert validation.
    from algo_trading.core import backtester
    backtester._chain_lookup = None
    backtester.reset_chain_cache()
    for ticker, symbol in symbols.items():
        if symbol:
            backtester._chain_lookups[symbol] = lookups[ticker]
    report_dir = ROOT / "reports" / "signal-data"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"asof": asof.isoformat(), "directory": str(folder), "underlyings": status}
    (report_dir / f"{asof}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch exact-session option data without sending alerts")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare_signal_data(args.as_of, download=not args.no_download), indent=2))
