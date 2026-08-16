"""
tests/test_pricing.py — Verification for Black-Scholes formulas and Greeks.
"""

from datetime import date
import pytest
from algo_trading.core.pricing import (
    bs_price,
    bs_greeks,
    get_atm_strike,
    get_strike_by_delta,
    calculate_transaction_cost,
)


def test_bs_price_call_put_parity():
    """Verify call and put prices are non-negative and ATM values are plausible."""
    S, K, T, r, sigma = 22000, 22000, 30 / 365, 0.065, 0.15
    call_px = bs_price(S, K, T, r, sigma, "CE")
    put_px = bs_price(S, K, T, r, sigma, "PE")

    assert call_px > 0
    assert put_px > 0
    # For ATM with positive interest rate, Call is slightly higher than Put
    assert call_px > put_px


def test_bs_greeks_delta_bounds():
    """Verify delta bounds for Call (0 to 1) and Put (-1 to 0)."""
    S, K, T, r, sigma = 22000, 22000, 30 / 365, 0.065, 0.15
    call_greeks = bs_greeks(S, K, T, r, sigma, "CE")
    put_greeks = bs_greeks(S, K, T, r, sigma, "PE")

    assert 0.0 <= call_greeks["delta"] <= 1.0
    assert -1.0 <= put_greeks["delta"] <= 0.0
    assert call_greeks["gamma"] > 0
    assert call_greeks["vega"] > 0


def test_atm_strike_rounding():
    """Verify strike rounding logic."""
    assert get_atm_strike(22024.5, step=50) == 22000.0
    assert get_atm_strike(22026.0, step=50) == 22050.0
    assert get_atm_strike(48120.0, step=100) == 48100.0


def test_calculate_transaction_cost():
    """Verify brokerage calculation produces positive non-zero cost."""
    cost = calculate_transaction_cost(premium=250.0, lot_size=25, num_lots=2, side="buy")
    assert cost > 0


def test_sell_transaction_cost_includes_sell_side_charges():
    """Sell orders should include STT and no buy-side stamp duty."""
    buy_cost = calculate_transaction_cost(premium=250.0, lot_size=25, num_lots=2, side="buy")
    sell_cost = calculate_transaction_cost(premium=250.0, lot_size=25, num_lots=2, side="sell")

    assert sell_cost > buy_cost
