"""Strict local bar/quote ingestion. Timestamps denote completed bars and observed quotes."""
import numpy as np
import pandas as pd


def load_intraday_csv(path, quotes=False):
    data = pd.read_csv(path)
    required = (["timestamp", "underlying", "expiry", "strike", "option_type", "bid", "ask", "bid_size", "ask_size"]
                if quotes else ["timestamp", "Open", "High", "Low", "Close", "Volume"])
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError(f"Missing intraday columns: {sorted(missing)}")
    timestamps = pd.to_datetime(data.pop("timestamp"))
    if timestamps.dt.tz is None:
        raise ValueError("Intraday timestamps require explicit timezone offsets")
    data.index = pd.DatetimeIndex(timestamps).tz_convert("Asia/Kolkata")
    data = data.sort_index()
    numeric = ["strike", "bid", "ask", "bid_size", "ask_size"] if quotes else ["Open", "High", "Low", "Close", "Volume"]
    for col in numeric:
        data[col] = pd.to_numeric(data[col], errors="raise")
    if not np.isfinite(data[numeric].to_numpy()).all():
        raise ValueError("Non-finite intraday data")
    if quotes:
        data["expiry"] = pd.to_datetime(data["expiry"]).dt.date
        if not data.option_type.isin(["CE", "PE"]).all() or (data.strike <= 0).any():
            raise ValueError("Invalid option contract")
        if ((data.bid < 0) | (data.ask <= 0) | (data.ask < data.bid) | (data.bid_size < 0) | (data.ask_size < 0)).any():
            raise ValueError("Invalid quote or crossed market")
        keys = data.reset_index().columns[0]
        if data.reset_index().duplicated([keys, "underlying", "expiry", "strike", "option_type"]).any():
            raise ValueError("Duplicate contract quote timestamp")
    else:
        if data.index.has_duplicates or (data.Volume < 0).any():
            raise ValueError("Duplicate bar or negative volume")
        if ((data.Low <= 0) | (data.High < data[["Open", "Close", "Low"]].max(axis=1)) |
            (data.Low > data[["Open", "Close"]].min(axis=1))).any():
            raise ValueError("Invalid OHLC bar")
    return data


def session_signals(bars, strategy="orb", opening_minutes=15):
    """Completed-bar signals. VWAP resets daily; ORB freezes the opening interval."""
    if strategy not in {"orb", "vwap"}:
        raise ValueError("Intraday strategy must be orb or vwap")
    if bars.index.tz is None:
        raise ValueError("Timezone-aware bars required")
    result = pd.Series(0, index=bars.index, dtype=int)
    for session, frame in bars.groupby(bars.index.date):
        start = pd.Timestamp(str(session) + " 09:15", tz="Asia/Kolkata")
        end = start + pd.Timedelta(minutes=opening_minutes)
        if strategy == "orb":
            opening = frame.loc[(frame.index > start) & (frame.index <= end)]
            if opening.empty or opening.index.max() != end:
                continue
            high, low = opening.High.max(), opening.Low.min()
            after = frame.loc[frame.index > end]
            above, below = after.Close > high, after.Close < low
            result.loc[after.index] = (above & ~above.shift(1, fill_value=False)).astype(int) - (below & ~below.shift(1, fill_value=False)).astype(int)
        else:
            typical = (frame.High + frame.Low + frame.Close) / 3
            vwap = (typical * frame.Volume).cumsum() / frame.Volume.cumsum().replace(0, float("nan"))
            diff = frame.Close - vwap
            result.loc[frame.index] = ((diff > 0) & (diff.shift() <= 0)).astype(int) - ((diff < 0) & (diff.shift() >= 0)).astype(int)
    return result
