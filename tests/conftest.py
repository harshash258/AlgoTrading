"""
tests/conftest.py — Pytest fixtures and mock market datasets.
"""

import sys
import os
from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest

# Ensure root is on path for testing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def sample_market_data():
    """Generates synthetic daily OHLCV + VIX DataFrame for testing."""
    np.random.seed(42)
    dates = pd.date_range(start="2023-01-01", periods=100, freq="B")
    
    # Random walk around 20,000
    returns = np.random.normal(0.0005, 0.01, size=len(dates))
    close = 20000.0 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(np.random.normal(0, 0.005, size=len(dates))))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, size=len(dates))))
    open_p = (high + low) / 2
    volume = np.random.randint(100_000, 1_000_000, size=len(dates))
    vix = np.random.uniform(12.0, 18.0, size=len(dates))

    df = pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
        "VIX": vix,
    }, index=dates)

    return {"^NSEI": df}
