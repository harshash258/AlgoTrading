"""Market-data boundary: replay never exposes observations beyond the decision time."""
from typing import Protocol
import pandas as pd
from algo_trading.data.intraday import load_intraday_csv


class MarketDataProvider(Protocol):
    name: str
    def candles(self, underlying: str, asof: pd.Timestamp) -> pd.DataFrame: ...
    def quotes(self, underlying: str, asof: pd.Timestamp) -> pd.DataFrame: ...


class ReplayProvider:
    name = "offline_csv_replay"

    def __init__(self, bars_by_underlying, quotes_path):
        self._bars = {ticker: load_intraday_csv(path) for ticker, path in bars_by_underlying.items()}
        self._quotes = load_intraday_csv(quotes_path, quotes=True)

    def candles(self, underlying, asof):
        frame = self._bars.get(underlying, pd.DataFrame())
        return frame.loc[frame.index <= asof].copy() if not frame.empty else frame.copy()

    def quotes(self, underlying, asof):
        q = self._quotes
        return q.loc[(q.index <= asof) & (q.underlying == underlying)].copy()

class UpstoxProvider:
    """Read-only authenticated data. Instrument keys must come from a listed master.

    REST quote timestamps are preserved; response arrival never refreshes stale quotes.
    Only current sessions are supported. Persist observations for historical replay.
    """
    name = "upstox_rest"

    def __init__(self, token, instruments, session=None):
        import requests
        if not token:
            raise ValueError("Set UPSTOX_ACCESS_TOKEN locally; do not put tokens in report files")
        self._token = token
        self.instruments = instruments
        self._session = session or requests.Session()

    def _get(self, path, params=None):
        response = self._session.get("https://api.upstox.com" + path, params=params,
            headers={"Authorization": "Bearer " + self._token, "Accept": "application/json"}, timeout=15)
        if response.status_code != 200:
            raise ValueError(f"Upstox data request failed (HTTP {response.status_code})")
        payload = response.json()
        if payload.get("status") != "success":
            raise ValueError("Upstox data response unsuccessful")
        return payload["data"]

    def candles(self, underlying, asof):
        from urllib.parse import quote
        key = self.instruments["underlyings"][underlying]
        data = self._get("/v3/historical-candle/intraday/" + quote(key, safe="") + "/minutes/1")
        rows = data.get("candles", [])
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame([r[:6] for r in rows], columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
        # Vendor timestamp is interval START; our contract is completed interval END.
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("timestamp"), utc=True)).tz_convert("Asia/Kolkata") + pd.Timedelta(minutes=1)
        frame = frame.sort_index()
        if frame.index.has_duplicates or not frame.apply(lambda s: pd.to_numeric(s, errors="coerce").notna().all()).all():
            raise ValueError("Malformed provider candles")
        import numpy as np
        if not np.isfinite(frame.to_numpy(dtype=float)).all() or (frame.Volume < 0).any() or (frame.Low <= 0).any() or (frame.High < frame[["Open", "Low", "Close"]].max(axis=1)).any() or (frame.Low > frame[["Open", "Close"]].min(axis=1)).any():
            raise ValueError("Invalid provider OHLCV")
        return frame.loc[(frame.index <= asof) & (frame.index.date == asof.date())].copy()

    def quotes(self, underlying, asof):
        records = [r for r in self.instruments["contracts"] if r["underlying"] == underlying]
        rows = []
        for offset in range(0, len(records), 100):
            batch = records[offset:offset + 100]
            data = self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(r["instrument_key"] for r in batch)})
            by_key = {v["instrument_token"]: v for v in data.values()}
            for record in batch:
                q = by_key.get(record["instrument_key"])
                if not q or not q.get("timestamp"):
                    continue
                timestamp = pd.Timestamp(q["timestamp"])
                if timestamp.tzinfo is None or timestamp > asof:
                    continue
                buy, sell = q["depth"]["buy"], q["depth"]["sell"]
                if not buy or not sell:
                    continue
                rows.append(dict(timestamp=timestamp, underlying=underlying,
                    expiry=pd.Timestamp(record["expiry"]).date(), strike=float(record["strike"]),
                    option_type=record["option_type"], bid=buy[0]["price"], ask=sell[0]["price"],
                    bid_size=buy[0]["quantity"], ask_size=sell[0]["quantity"]))
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows).set_index("timestamp").sort_index()
        if frame.reset_index().duplicated(["timestamp", "underlying", "expiry", "strike", "option_type"]).any():
            raise ValueError("Duplicate provider instrument mapping")
        return frame

class CapturedProvider:
    """Immutable observation batch with a decision timestamp AFTER data acquisition."""
    def __init__(self, name, bars, quotes, asof):
        self.name, self._bars, self._quotes, self.asof = name, bars, quotes, asof

    def candles(self, underlying, asof):
        frame = self._bars.get(underlying, pd.DataFrame())
        return frame.loc[frame.index <= asof].copy() if not frame.empty else frame.copy()

    def quotes(self, underlying, asof):
        frame = self._quotes.get(underlying, pd.DataFrame())
        return frame.loc[frame.index <= asof].copy() if not frame.empty else frame.copy()


def capture_live(provider, underlyings):
    bars, quotes = {}, {}
    for ticker in underlyings:
        # Final decision time is recorded after acquisition, not before the request.
        # Engine still discards incomplete candles/future vendor timestamps.
        horizon = pd.Timestamp.now(tz="Asia/Kolkata") + pd.Timedelta(minutes=1)
        try:
            bars[ticker] = provider.candles(ticker, horizon)
            quotes[ticker] = provider.quotes(ticker, horizon)
        except (ValueError, KeyError, OSError):
            bars[ticker], quotes[ticker] = pd.DataFrame(), pd.DataFrame()
    return CapturedProvider(provider.name, bars, quotes, pd.Timestamp.now(tz="Asia/Kolkata"))
