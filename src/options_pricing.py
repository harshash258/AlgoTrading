"""
options_pricing.py — Black-Scholes options pricing and Greeks calculation.

Used to reconstruct historical option premiums synthetically since free
historical options chain data is not available for Indian markets.

All functions are stateless and work on scalar or numpy array inputs.
"""

import numpy as np
from scipy.stats import norm
from datetime import date, timedelta
import logging

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Core Black-Scholes
# ─────────────────────────────────────────────────────────────────

def bs_price(
    S: float,       # Spot price
    K: float,       # Strike price
    T: float,       # Time to expiry in years
    r: float,       # Risk-free rate (decimal, annualised)
    sigma: float,   # Implied volatility (decimal, annualised)
    option_type: str,  # "CE" or "PE"
) -> float:
    """
    Black-Scholes price for a European option.
    Returns 0.0 if T <= 0 or sigma <= 0 (degenerate case).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # Intrinsic value at expiry
        if option_type.upper() == "CE":
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type.upper() == "CE":
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:  # PE
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    return max(float(price), 0.0)


def bs_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> dict:
    """
    Calculate Black-Scholes Greeks.

    Returns dict with keys: delta, gamma, theta, vega, rho
    theta is per calendar day (not per year).
    """
    if T <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    pdf_d1 = norm.pdf(d1)

    gamma = pdf_d1 / (S * sigma * np.sqrt(T))
    vega  = S * pdf_d1 * np.sqrt(T) / 100  # per 1% move in IV

    if option_type.upper() == "CE":
        delta = norm.cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2 * np.sqrt(T))
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        ) / 365
        rho = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
    else:  # PE
        delta = norm.cdf(d1) - 1
        theta = (
            -(S * pdf_d1 * sigma) / (2 * np.sqrt(T))
            + r * K * np.exp(-r * T) * norm.cdf(-d2)
        ) / 365
        rho = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "theta": float(theta),
        "vega":  float(vega),
        "rho":   float(rho),
    }


# ─────────────────────────────────────────────────────────────────
# Strike selection helpers
# ─────────────────────────────────────────────────────────────────

def get_atm_strike(spot: float, step: float = 50.0) -> float:
    """
    Return the ATM strike rounded to the nearest step.
    Default step = 50 (Nifty strike intervals).
    """
    return round(spot / step) * step


def get_strike_by_delta(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_delta: float,
    option_type: str,
    step: float = 50.0,
) -> float:
    """
    Find the strike closest to the target absolute delta.
    Searches strikes in a range around spot and returns the best match.

    target_delta: e.g. 0.20 for 20-delta OTM option
    """
    atm = get_atm_strike(S, step)
    # Search range: 20 strikes either side of ATM
    candidates = [atm + i * step for i in range(-20, 21)]
    best_strike = atm
    best_diff   = float("inf")

    for K in candidates:
        if K <= 0:
            continue
        g = bs_greeks(S, K, T, r, sigma, option_type)
        diff = abs(abs(g["delta"]) - target_delta)
        if diff < best_diff:
            best_diff   = diff
            best_strike = K

    return best_strike


# ─────────────────────────────────────────────────────────────────
# Expiry helpers
# ─────────────────────────────────────────────────────────────────

def last_thursday(year: int, month: int) -> date:
    """Return the last Thursday of the given month (monthly expiry)."""
    # Start from last day of month and walk back to Thursday
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 3) % 7  # 3 = Thursday
    return last_day - timedelta(days=offset)


def next_expiry(from_date: date, weekly: bool = False) -> date:
    """
    Return the next expiry date from a given date.

    weekly=True  → next Thursday (for Nifty/BankNifty weekly expiry)
    weekly=False → last Thursday of current or next month
    """
    if weekly:
        days_until_thursday = (3 - from_date.weekday()) % 7
        if days_until_thursday == 0:
            days_until_thursday = 7  # same day counts as next week
        return from_date + timedelta(days=days_until_thursday)
    else:
        # Monthly: try this month first, then next
        exp = last_thursday(from_date.year, from_date.month)
        if exp <= from_date:
            if from_date.month == 12:
                exp = last_thursday(from_date.year + 1, 1)
            else:
                exp = last_thursday(from_date.year, from_date.month + 1)
        return exp


def time_to_expiry(from_date: date, expiry: date) -> float:
    """Return time to expiry in years (calendar days / 365)."""
    delta = (expiry - from_date).days
    return max(delta / 365.0, 0.0)


# ─────────────────────────────────────────────────────────────────
# Brokerage & cost calculator
# ─────────────────────────────────────────────────────────────────

def calculate_transaction_cost(
    premium: float,
    lot_size: int,
    num_lots: int,
    side: str,  # "buy" or "sell"
) -> float:
    """
    Calculate total transaction cost for one options order.

    Returns cost in ₹ to be deducted from P&L.
    Based on Groww's fee structure as defined in config.
    """
    turnover = premium * lot_size * num_lots

    # Brokerage: ₹20 flat or 0.05% whichever is lower
    brokerage = min(config.BROKERAGE_PER_ORDER, turnover * config.BROKERAGE_MAX_PCT / 100)

    # STT
    stt_rate = config.STT_SELL_PCT if side == "sell" else config.STT_BUY_PCT
    stt = turnover * stt_rate / 100

    # Exchange charges
    exchange = turnover * config.EXCHANGE_CHARGE_PCT / 100

    # GST on brokerage + exchange
    gst = (brokerage + exchange) * config.GST_PCT / 100

    # SEBI charges
    sebi = (turnover / 1e7) * config.SEBI_CHARGE_PER_CR

    # Stamp duty (buy side only)
    stamp = (turnover * config.STAMP_DUTY_BUY_PCT / 100) if side == "buy" else 0.0

    total = brokerage + stt + exchange + gst + sebi + stamp
    return round(total, 2)


# ─────────────────────────────────────────────────────────────────
# Quick sanity check
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example: Nifty at 22000, ATM CE, 30 days to expiry, IV=15%
    S, K, T, r, sigma = 22000, 22000, 30/365, 0.065, 0.15
    price = bs_price(S, K, T, r, sigma, "CE")
    greeks = bs_greeks(S, K, T, r, sigma, "CE")
    cost = calculate_transaction_cost(price, 25, 1, "buy")

    print(f"Nifty ATM CE @ {S}, Strike {K}, T={T:.4f}y, IV={sigma*100}%")
    print(f"  Premium : ₹{price:.2f}")
    print(f"  Delta   : {greeks['delta']:.4f}")
    print(f"  Gamma   : {greeks['gamma']:.6f}")
    print(f"  Theta   : ₹{greeks['theta']:.2f}/day")
    print(f"  Vega    : ₹{greeks['vega']:.2f}/1% IV")
    print(f"  Txn cost: ₹{cost:.2f} (buy, 1 lot of 25)")

    exp = next_expiry(date.today(), weekly=True)
    print(f"\n  Next weekly expiry: {exp}")
    exp_m = next_expiry(date.today(), weekly=False)
    print(f"  Next monthly expiry: {exp_m}")
