"""
test_screener_optimization.py — Unit tests for the optimized screening architecture.

Tests the two-workflow architecture:
  1. Monthly update_fundamentals.py: cache fundamentals to CSV
  2. Biweekly stock_screener.py: read cache + bulk price fetch + dynamic ratios
"""

import pytest
import os
import csv
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algo_trading.screener.stock_screener import (
    ScreeningStrategy,
    StockScreener,
    compute_dynamic_metrics,
    STRATEGY_CHEAP_TO_MOON,
    STRATEGY_MULTIBAGGER,
)


class TestScreeningStrategy:
    """Test strategy validation logic."""

    def test_cheap_to_moon_validation_pass(self):
        """Test that a stock passing cheap_to_moon criteria is correctly identified."""
        metrics = {
            "pb": 0.8,  # < 1.0 ✓
            "pe": 10.0,  # > 0.5 ✓
            "price": 50.0,  # < 100 ✓
            "up_52w_pct": 15.0,  # > 10 ✓
            "market_cap_cr": 50.0,  # > 20 ✓
            "roce": 8.0,  # > 5 ✓
        }

        passes, reasons = STRATEGY_CHEAP_TO_MOON.validate_stock(metrics)
        assert passes is True
        assert all(r.startswith("✓") for r in reasons), "All rules should pass"

    def test_cheap_to_moon_validation_fail(self):
        """Test that a stock failing criteria is correctly rejected."""
        metrics = {
            "pb": 1.5,  # > 1.0 ✗
            "pe": 10.0,  # > 0.5 ✓
            "price": 150.0,  # > 100 ✗
            "up_52w_pct": 5.0,  # < 10 ✗
            "market_cap_cr": 50.0,  # > 20 ✓
            "roce": 8.0,  # > 5 ✓
        }

        passes, reasons = STRATEGY_CHEAP_TO_MOON.validate_stock(metrics)
        assert passes is False
        assert any(r.startswith("✗") for r in reasons), "Some rules should fail"

    def test_multibagger_validation_pass(self):
        """Test that a multibagger stock meeting criteria passes."""
        metrics = {
            "market_cap_cr": 500.0,  # 100-2500 ✓
            "sales_cr": 200.0,  # > 100 ✓
            "sales_growth_3yr": 18.0,  # > 15 ✓
            "profit_growth_3yr": 20.0,  # > 15 ✓
            "roce": 18.0,  # > 15 ✓
            "roe": 16.0,  # > 15 ✓
            "debt_to_equity": 0.4,  # < 0.5 ✓
            "promoter_holding": 45.0,  # > 40 ✓
            "pledged_pct": 3.0,  # < 5 ✓
            "up_52w_pct": 12.0,  # > 10 ✓
        }

        passes, reasons = STRATEGY_MULTIBAGGER.validate_stock(metrics)
        assert passes is True
        assert all(r.startswith("✓") for r in reasons)


class TestDynamicMetricsComputation:
    """Test dynamic metric computation from cached + current price."""

    def test_dynamic_pe_calculation(self):
        """Test dynamic P/E ratio computation from price and EPS."""
        cached = {
            "ticker": "TESTINC.NS",
            "eps": 50.0,
            "book_value": 100.0,
            "roce": 15.0,
            "roe": 16.0,
            "market_cap_cr": 500.0,
        }

        current_price = {
            "price": 1000.0,
            "up_52w_pct": 12.0,
        }

        metrics = compute_dynamic_metrics(
            "TESTINC.NS",
            {"TESTINC.NS": cached},
            {"TESTINC.NS": current_price},
        )

        # Dynamic P/E should be price / eps = 1000 / 50 = 20
        assert metrics["pe"] == pytest.approx(20.0, rel=0.01)
        assert metrics["price"] == 1000.0
        assert metrics["up_52w_pct"] == 12.0

    def test_dynamic_pb_calculation(self):
        """Test dynamic P/B ratio computation from price and book value."""
        cached = {
            "ticker": "BANKCO.NS",
            "eps": 75.0,
            "book_value": 500.0,
            "roce": 18.0,
            "roe": 20.0,
        }

        current_price = {
            "price": 1500.0,
            "up_52w_pct": 8.0,
        }

        metrics = compute_dynamic_metrics(
            "BANKCO.NS",
            {"BANKCO.NS": cached},
            {"BANKCO.NS": current_price},
        )

        # Dynamic P/B should be price / book_value = 1500 / 500 = 3.0
        assert metrics["pb"] == pytest.approx(3.0, rel=0.01)

    def test_fallback_to_cached_price_when_no_current(self):
        """Test fallback to cached price when current price not available."""
        cached = {
            "ticker": "OLDSTOCK.NS",
            "price": 100.0,
            "pe": 15.0,
            "pb": 1.2,
            "eps": 6.66,
            "book_value": 83.3,
        }

        metrics = compute_dynamic_metrics(
            "OLDSTOCK.NS",
            {"OLDSTOCK.NS": cached},
            {},  # No current price data
        )

        # Should use cached price/ratios as fallback
        assert metrics["price"] == 100.0
        assert metrics["pe"] == 15.0
        assert metrics["pb"] == 1.2

    def test_missing_eps_prevents_dynamic_pe(self):
        """Test that missing EPS prevents dynamic P/E calculation."""
        cached = {
            "ticker": "NOEPS.NS",
            "eps": None,
            "book_value": 100.0,
        }

        current_price = {
            "price": 500.0,
            "up_52w_pct": 5.0,
        }

        metrics = compute_dynamic_metrics(
            "NOEPS.NS",
            {"NOEPS.NS": cached},
            {"NOEPS.NS": current_price},
        )

        # Without EPS, PE should be None
        assert metrics["pe"] is None
        assert metrics["price"] == 500.0


class TestStockScreener:
    """Test the main StockScreener class."""

    def test_screener_initialization(self):
        """Test screener can be initialized with strategies."""
        screener = StockScreener()
        screener.add_strategy(STRATEGY_CHEAP_TO_MOON)
        screener.add_strategy(STRATEGY_MULTIBAGGER)

        assert len(screener.strategies) == 2
        assert screener.strategies[0].name == "Cheap to Moon"
        assert screener.strategies[1].name == "Multibagger"

    def test_add_strategies_by_name(self):
        """Test adding strategies by name from registry."""
        screener = StockScreener()
        screener.add_strategies(["cheap_to_moon", "multibagger"])

        assert len(screener.strategies) == 2

    def test_load_universe_from_file(self):
        """Test loading ticker universe from file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("# Comment line\n")
            f.write("RELIANCE.NS\n")
            f.write("TCS.NS\n")
            f.write("INFY.NS\n")
            f.flush()
            temp_file = f.name

        try:
            screener = StockScreener()
            screener.load_nse_universe(temp_file)

            assert len(screener._nse_universe) == 3
            assert "RELIANCE.NS" in screener._nse_universe
            assert "TCS.NS" in screener._nse_universe
            assert "INFY.NS" in screener._nse_universe

        finally:
            os.unlink(temp_file)

    def test_universe_filters_comments_and_blanks(self):
        """Test that universe loader ignores comments and blank lines."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("STOCK1.NS\n")
            f.write("# This is a comment\n")
            f.write("\n")  # Blank line
            f.write("  \n")  # Whitespace only
            f.write("STOCK2.NS\n")
            f.flush()
            temp_file = f.name

        try:
            screener = StockScreener()
            screener.load_nse_universe(temp_file)

            assert len(screener._nse_universe) == 2
            assert "STOCK1.NS" in screener._nse_universe
            assert "STOCK2.NS" in screener._nse_universe

        finally:
            os.unlink(temp_file)


class TestPerformance:
    """Test performance characteristics of the new architecture."""

    def test_dynamic_metric_computation_speed(self):
        """Test that dynamic metric computation is fast (<1ms per stock)."""
        import time

        cached_data = {
            "STOCK{}.NS".format(i): {
                "ticker": "STOCK{}.NS".format(i),
                "eps": 50.0,
                "book_value": 100.0,
                "roce": 15.0,
                "roe": 16.0,
                "market_cap_cr": 500.0,
            }
            for i in range(100)
        }

        price_data = {
            "STOCK{}.NS".format(i): {
                "price": 1000.0 + i,
                "up_52w_pct": 10.0 + i % 20,
            }
            for i in range(100)
        }

        start = time.time()
        for i in range(100):
            ticker = "STOCK{}.NS".format(i)
            compute_dynamic_metrics(ticker, cached_data, price_data)
        elapsed = time.time() - start

        avg_per_stock = (elapsed * 1000) / 100  # milliseconds per stock
        assert avg_per_stock < 1.0, f"Should be <1ms per stock, got {avg_per_stock}ms"
        print(f"Dynamic metric computation: {avg_per_stock:.2f}ms per stock")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
