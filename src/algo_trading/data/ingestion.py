"""
data_ingestion.py — Downloads and caches historical spot price and VIX data.

Uses yfinance as the primary source. Data is cached locally in CSV files
to avoid redundant downloads on subsequent runs.
"""

import os
import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# Default VIX fallback used when ^INDIAVIX is completely unavailable
_VIX_FALLBACK = 15.0

# ─────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────

def _cache_path(ticker: str, suffix: str = "ohlcv") -> str:
    """Return the local CSV path for a given ticker."""
    safe = ticker.replace("^", "").replace(".", "_")
    os.makedirs(config.RAW_DATA_DIR, exist_ok=True)
    return os.path.join(config.RAW_DATA_DIR, f"{safe}_{suffix}.csv")


def _load_cache(ticker: str, suffix: str = "ohlcv") -> pd.DataFrame | None:
    """Load cached data if it exists."""
    path = _cache_path(ticker, suffix)
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        logger.debug(f"Loaded cache: {path} ({len(df)} rows)")
        return df
    return None


def _save_cache(df: pd.DataFrame, ticker: str, suffix: str = "ohlcv") -> None:
    """Save DataFrame to local CSV cache."""
    path = _cache_path(ticker, suffix)
    df.to_csv(path)
    logger.debug(f"Saved cache: {path} ({len(df)} rows)")


# ─────────────────────────────────────────────────────────────────
# Core download functions
# ─────────────────────────────────────────────────────────────────

def fetch_ohlcv(
    ticker: str,
    start: date = config.BACKTEST_START,
    end: date = config.BACKTEST_END,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV data for a ticker.

    Returns a DataFrame with columns: Open, High, Low, Close, Volume
    Index: DatetimeIndex (UTC dates)

    Caches result locally. On subsequent calls, loads from cache unless
    force_refresh=True or cached data is stale (missing recent dates).
    """
    cached = _load_cache(ticker)

    if cached is not None and not force_refresh:
        cached_end = cached.index.max().date()
        # If cache covers our required range, return it
        if cached_end >= end:
            mask = (cached.index.date >= start) & (cached.index.date <= end)
            return cached.loc[mask].copy()
        # Otherwise do an incremental fetch from the day after cached end
        incremental_start = cached_end + timedelta(days=1)
        logger.info(f"{ticker}: Incrementally fetching {incremental_start} → {end}")
        new_data = _download(ticker, incremental_start, end)
        if new_data is not None and not new_data.empty:
            combined = pd.concat([cached, new_data])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
            _save_cache(combined, ticker)
            mask = (combined.index.date >= start) & (combined.index.date <= end)
            return combined.loc[mask].copy()
        mask = (cached.index.date >= start) & (cached.index.date <= end)
        return cached.loc[mask].copy()

    logger.info(f"{ticker}: Downloading {start} → {end}")
    df = _download(ticker, start, end)
    if df is None or df.empty:
        # Download failed — try an incremental update on top of the existing cache
        # before giving up. Useful when yfinance returns empty for a known-flaky
        # ticker (e.g. ^INDIAVIX) even though recent data is available.
        if cached is not None:
            cached_end = cached.index.max().date()
            incremental_start = cached_end + timedelta(days=1)
            if incremental_start <= end:
                logger.warning(
                    f"{ticker}: Full refresh failed. Trying incremental "
                    f"{incremental_start} → {end} on top of cached data."
                )
                new_data = _download(ticker, incremental_start, end)
                if new_data is not None and not new_data.empty:
                    combined = pd.concat([cached, new_data])
                    combined = combined[~combined.index.duplicated(keep="last")]
                    combined.sort_index(inplace=True)
                    _save_cache(combined, ticker)
                    mask = (combined.index.date >= start) & (combined.index.date <= end)
                    return combined.loc[mask].copy()
            # Incremental also failed or not needed — serve cache as-is
            logger.warning(
                f"{ticker}: Download failed. Serving cached data "
                f"(last date: {cached_end})."
            )
            mask = (cached.index.date >= start) & (cached.index.date <= end)
            sliced = cached.loc[mask].copy()
            if not sliced.empty:
                return sliced
        raise ValueError(f"No data returned for {ticker} between {start} and {end}")
    _save_cache(df, ticker)
    return df.copy()


def _download(ticker: str, start: date, end: date) -> pd.DataFrame | None:
    """Raw yfinance download with up to 3 retries on failure."""
    for attempt in range(1, 4):
        try:
            raw = yf.download(
                ticker,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),  # yf end is exclusive
                progress=False,
                auto_adjust=True,
            )
            if raw.empty:
                logger.warning(
                    f"yfinance returned empty data for {ticker} "
                    f"(attempt {attempt}/3)"
                )
                if attempt < 3:
                    time.sleep(5 * attempt)  # 5s, 10s back-off
                    continue
                return None

            # Flatten MultiIndex columns if present (yfinance >= 0.2.x)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index)
            df.dropna(subset=["Close"], inplace=True)
            return df

        except Exception as e:
            logger.error(f"Failed to download {ticker} (attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(5 * attempt)

    return None


def fetch_india_vix(
    start: date = config.BACKTEST_START,
    end: date = config.BACKTEST_END,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch India VIX data (^INDIAVIX from Yahoo Finance).

    Returns DataFrame with columns 'VIX' and 'VIX_decimal' indexed by date.
    VIX is used as implied volatility proxy for options pricing.

    Fallback chain when ^INDIAVIX is unavailable:
      1. Existing local cache (served as-is)
      2. Constant _VIX_FALLBACK value (15.0) — signals still fire, just with
         a fixed IV assumption. A warning is included in the Telegram message
         via the staleness check in telegram_notify.py.
    """
    try:
        df = fetch_ohlcv(config.INDIA_VIX_TICKER, start, end, force_refresh)
        vix = df[["Close"]].rename(columns={"Close": "VIX"})
        vix["VIX_decimal"] = vix["VIX"] / 100.0
        return vix
    except Exception as e:
        logger.warning(
            f"Could not fetch India VIX ({e}). "
            f"Using fallback VIX = {_VIX_FALLBACK}%. "
            f"Signal generation will continue but options pricing is approximate."
        )
        # Build a constant-VIX DataFrame spanning the requested date range
        # using business days so it aligns with the spot data index on join.
        idx = pd.date_range(start=str(start), end=str(end), freq="B")
        vix = pd.DataFrame(
            {"VIX": _VIX_FALLBACK, "VIX_decimal": _VIX_FALLBACK / 100.0},
            index=idx,
        )
        return vix


def fetch_all_underlyings(
    start: date = config.BACKTEST_START,
    end: date = config.BACKTEST_END,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for all tickers defined in config.UNDERLYINGS.

    Returns a dict: { ticker: DataFrame }
    """
    data = {}
    for ticker in config.UNDERLYINGS:
        try:
            df = fetch_ohlcv(ticker, start, end, force_refresh)
            data[ticker] = df
            logger.info(f"  {ticker}: {len(df)} trading days loaded")
        except Exception as e:
            logger.error(f"  {ticker}: FAILED — {e}")
    return data


def get_combined_dataset(
    start: date = config.BACKTEST_START,
    end: date = config.BACKTEST_END,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Convenience function: returns spot data + VIX merged per underlying.

    Each value in the returned dict is a DataFrame with columns:
    Open, High, Low, Close, Volume, VIX, VIX_decimal
    """
    spot_data = fetch_all_underlyings(start, end, force_refresh)
    vix = fetch_india_vix(start, end, force_refresh)

    combined = {}
    for ticker, df in spot_data.items():
        merged = df.join(vix, how="left")
        # Forward-fill VIX for days VIX data might be missing
        merged["VIX"] = merged["VIX"].ffill()
        merged["VIX_decimal"] = merged["VIX_decimal"].ffill()
        combined[ticker] = merged

    return combined


# ─────────────────────────────────────────────────────────────────
# Quick sanity check
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Fetching data for all underlyings...\n")
    datasets = get_combined_dataset()
    for ticker, df in datasets.items():
        print(f"{ticker}: {len(df)} rows | {df.index.min().date()} → {df.index.max().date()}")
        print(df.tail(3))
        print()


# ─────────────────────────────────────────────────────────────────
# NSE Bhavcopy Option Chain Loader
# ─────────────────────────────────────────────────────────────────
# NSE publishes free daily option chain files at:
#   https://www.nseindia.com/market-data/historical-derivatives-data
# Download the ZIP files and place them in a folder.
# Pass that folder to load_bhavcopy_folder() to get real historical premiums.
# This replaces synthetic Black-Scholes pricing for backtests when available.

import glob
import zipfile
import numpy as np

# Maps canonical column names → possible NSE schema variants across years
_COLUMN_ALIASES = {
    "symbol"    : ["SYMBOL", "TckrSymb", "TICKER"],
    "instrument": ["INSTRUMENT", "FinInstrmTp", "INSTRUMENT_TYPE"],
    "expiry"    : ["EXPIRY_DT", "XpryDt", "EXPIRY"],
    "strike"    : ["STRIKE_PR", "StrkPric", "STRIKE_PRICE"],
    "opt_type"  : ["OPTION_TYP", "OptnTp", "OPTION_TYPE"],
    "open"      : ["OPEN", "OpnPric"],
    "high"      : ["HIGH", "HghPric"],
    "low"       : ["LOW", "LwPric"],
    "close"     : ["CLOSE", "ClsPric"],
    "settle"    : ["SETTLE_PR", "SttlmPric"],
    "oi"        : ["OPEN_INT", "OpnIntrst"],
    "volume"    : ["CONTRACTS", "TtlTradgVol", "TtlTrfVal"],
    "date"      : ["TIMESTAMP", "TradDt", "DATE"],
}

# NSE futures instrument codes to exclude (keep only option rows)
_FUTURES_INSTRUMENTS = {
    "FUTIDX", "FUTSTK", "FUTIVX", "FUTCUR", "FUTIRT",
    "IDF", "STF", "CRF", "IRF",
}


def _map_bhavcopy_columns(cols: list) -> dict:
    """Map raw bhavcopy column names to canonical names."""
    found = {}
    for canon, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            for col in cols:
                if str(col).strip().upper() == alias.upper():
                    found[canon] = col
                    break
            if canon in found:
                break
    return found


def load_bhavcopy(
    path: str,
    symbol: str = "NIFTY",
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Load one NSE bhavcopy file (.csv or .csv.zip) and return normalised
    option rows for the given symbol.

    Returns DataFrame with columns:
        date, expiry, strike, opt_type ('CE'/'PE'),
        close, settle, oi, volume

    Parameters
    ----------
    path    : Path to .csv or .csv.zip bhavcopy file
    symbol  : NSE symbol to filter (default 'NIFTY')
    verbose : Print column mapping on first load (useful for debugging)
    """
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            csv_name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            with z.open(csv_name) as fh:
                raw = pd.read_csv(fh, low_memory=False)
    else:
        raw = pd.read_csv(path, low_memory=False)

    raw.columns = [str(c).strip() for c in raw.columns]
    col_map = _map_bhavcopy_columns(raw.columns)

    required = ("symbol", "expiry", "strike", "opt_type", "close")
    missing  = [k for k in required if k not in col_map]
    if missing:
        raise ValueError(
            f"Could not map columns {missing} in {os.path.basename(path)}.\n"
            f"Columns present: {list(raw.columns)}"
        )

    if verbose:
        logger.info(f"Bhavcopy column map: {col_map}")

    df = raw.rename(columns={v: k for k, v in col_map.items()})

    # Filter to requested symbol
    df = df[df["symbol"].astype(str).str.upper().str.strip() == symbol.upper()]

    # Drop futures rows (keep CE/PE only)
    if "instrument" in df.columns:
        df = df[~df["instrument"].astype(str).str.upper().str.strip().isin(_FUTURES_INSTRUMENTS)]

    df = df[df["opt_type"].astype(str).str.upper().str.strip().isin(["CE", "PE"])]

    # Parse dates and numerics
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce", dayfirst=True)
    df["date"]   = pd.to_datetime(df.get("date", pd.NaT), errors="coerce", dayfirst=True)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    for col in ("close", "settle", "oi", "volume", "open", "high", "low"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    keep_cols = [c for c in ["date","expiry","strike","opt_type","close","settle","oi","volume"] if c in df.columns]
    out = df[keep_cols].dropna(subset=["expiry", "strike", "close"])
    out = out[out["close"] > 0]
    return out.reset_index(drop=True)


def load_bhavcopy_folder(
    folder: str,
    symbol: str = "NIFTY",
    pattern: str = "*.zip",
) -> pd.DataFrame:
    """
    Load and concatenate every bhavcopy file in a folder.

    Parameters
    ----------
    folder  : Path to folder containing bhavcopy .zip or .csv files
    symbol  : NSE symbol to filter (default 'NIFTY')
    pattern : Glob pattern for files (default '*.zip')

    Returns
    -------
    Combined DataFrame sorted by date, expiry, strike, opt_type
    """
    files = sorted(glob.glob(os.path.join(folder, pattern)))
    if not files:
        raise FileNotFoundError(f"No files matching '{pattern}' in {folder}")

    logger.info(f"Loading {len(files)} bhavcopy files from {folder}...")
    frames, failed = [], []

    for i, f in enumerate(files):
        try:
            frames.append(load_bhavcopy(f, symbol, verbose=(i == 0)))
        except Exception as e:
            failed.append((os.path.basename(f), str(e)[:100]))

    if failed:
        logger.warning(f"{len(failed)} file(s) failed to load:")
        for name, err in failed[:5]:
            logger.warning(f"  {name}: {err}")

    if not frames:
        raise RuntimeError("All bhavcopy files failed to parse.")

    chain = pd.concat(frames, ignore_index=True)
    chain = chain.sort_values(["date", "expiry", "strike", "opt_type"])

    logger.info(
        f"Bhavcopy loaded: {len(chain):,} rows | "
        f"{chain['date'].min().date()} → {chain['date'].max().date()} | "
        f"{chain['expiry'].nunique()} expiries | "
        f"{chain['strike'].nunique()} strikes"
    )
    return chain.reset_index(drop=True)


class ChainLookup:
    """
    Fast (date, expiry, strike, opt_type) → premium lookup over a loaded
    bhavcopy chain. Use this to replace Black-Scholes synthetic pricing
    with real historical NSE premiums.

    Usage
    -----
    chain  = load_bhavcopy_folder("data/bhavcopy/", symbol="NIFTY")
    lookup = ChainLookup(chain)

    # Get real premium
    px = lookup.premium("2023-10-25", "2023-10-26", 19500, "CE")

    # Get nearest expiry at least 7 days out
    exp = lookup.nearest_expiry("2023-10-01", min_days=7)

    # Snap to nearest listed strike
    k = lookup.nearest_strike(19487)  # → 19500
    """

    def __init__(self, chain: pd.DataFrame, price_col: str = "close"):
        # Prefer settle price if close unavailable
        if price_col not in chain.columns:
            price_col = "settle" if "settle" in chain.columns else "close"
        self.price_col = price_col

        # Build fast indexed lookup
        self._idx = (
            chain
            .set_index(["date", "expiry", "strike", "opt_type"])[price_col]
            .pipe(lambda s: s[~s.index.duplicated(keep="last")])
            .sort_index()
        )

        self._dates    = np.array(sorted(chain["date"].dropna().unique()))
        self._expiries = np.array(sorted(chain["expiry"].dropna().unique()))
        self._strikes  = np.array(sorted(chain["strike"].dropna().unique()))

    def nearest_expiry(self, trade_date, min_days: int = 0) -> pd.Timestamp | None:
        """Return nearest expiry at least min_days after trade_date."""
        target = pd.Timestamp(trade_date) + pd.Timedelta(days=min_days)
        later  = self._expiries[self._expiries >= np.datetime64(target)]
        return pd.Timestamp(later[0]) if len(later) else None

    def nearest_strike(self, strike: float) -> float:
        """Snap a raw strike to the nearest listed strike in the chain."""
        return float(self._strikes[np.abs(self._strikes - strike).argmin()])

    def premium(
        self,
        trade_date,
        expiry,
        strike: float,
        opt_type: str,
    ) -> float | None:
        """
        Look up real premium from bhavcopy.
        Returns None if the (date, expiry, strike, type) combination is missing.
        """
        try:
            val = self._idx.loc[(
                pd.Timestamp(trade_date),
                pd.Timestamp(expiry),
                float(strike),
                opt_type,
            )]
            return float(val)
        except KeyError:
            return None

    def smile(
        self,
        trade_date,
        expiry,
        spot: float,
        moneyness_range: float = 0.06,
    ) -> pd.DataFrame:
        """
        Return IV smile for one date/expiry — useful for validating data quality.
        Rows within moneyness_range of spot (default ±6%) are returned.
        """
        rows = []
        for opt_type in ("CE", "PE"):
            for k in self._strikes:
                px = self.premium(trade_date, expiry, k, opt_type)
                if px is None or px <= 0:
                    continue
                moneyness = k / spot
                if abs(moneyness - 1.0) > moneyness_range:
                    continue
                rows.append({
                    "strike"   : k,
                    "type"     : opt_type,
                    "premium"  : px,
                    "moneyness": round(moneyness, 4),
                })
        return pd.DataFrame(rows).sort_values(["type", "strike"])

    def has_data_for(self, trade_date) -> bool:
        """Check if any data exists for a given date."""
        return pd.Timestamp(trade_date) in self._dates
