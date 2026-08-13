"""
algo_trading.data package — Data ingestion, caching, and bhavcopy retrieval.
"""

from algo_trading.data.ingestion import (
    fetch_ohlcv,
    fetch_india_vix,
    fetch_all_underlyings,
    get_combined_dataset,
    load_bhavcopy,
    load_bhavcopy_folder,
    ChainLookup,
)
from algo_trading.data.bhavcopy import (
    download_bhavcopy,
    download_range,
    download_last_n_days,
    verify_downloads,
)

__all__ = [
    "fetch_ohlcv",
    "fetch_india_vix",
    "fetch_all_underlyings",
    "get_combined_dataset",
    "load_bhavcopy",
    "load_bhavcopy_folder",
    "ChainLookup",
    "download_bhavcopy",
    "download_range",
    "download_last_n_days",
    "verify_downloads",
]
