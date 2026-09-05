"""
algo_trading.core.pricing — Black-Scholes options pricing and Greeks calculation.

Used to reconstruct historical option premiums synthetically since free
historical options chain data is not available for Indian markets.

All functions are stateless and work on scalar or numpy array inputs.
"""

import logging
from datetime import date, timedelta

import numpy as np
from scipy.stats import norm

from config import settings

logger = logging.getLogger(__name__)


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> float:
    """
    Black-Scholes price for a European option.
    Returns 0.0 if T <= 0 or sigma <= 0 (degenerate case).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if option_type.upper() == "CE":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type.upper() == "CE":
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
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

    Returns dict with keys: delta, gamma, theta, vega, rho.
    Theta is per calendar day.
    """
    if T <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    pdf_d1 = norm.pdf(d1)

    gamma = pdf_d1 / (S * sigma * np.sqrt(T))
    vega = S * pdf_d1 * np.sqrt(T) / 100

    if option_type.upper() == "CE":
        delta = norm.cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2 * np.sqrt(T))
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        ) / 365
        rho = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
    else:
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
        "vega": float(vega),
        "rho": float(rho),
    }


def get_atm_strike(spot: float, step: float = 50.0) -> float:
    """Return the ATM strike rounded to the nearest step."""
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
    """Find the strike closest to the target absolute delta."""
    atm = get_atm_strike(S, step)
    candidates = [atm + i * step for i in range(-20, 21)]
    best_strike = atm
    best_diff = float("inf")

    for K in candidates:
        if K <= 0:
            continue
        greeks = bs_greeks(S, K, T, r, sigma, option_type)
        diff = abs(abs(greeks["delta"]) - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_strike = K

    return best_strike


def last_tuesday(year: int, month: int) -> date:
    """Return the last Tuesday of the given month for NSE index expiry."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 1) % 7
    return last_day - timedelta(days=offset)


def last_thursday(year: int, month: int) -> date:
    """Legacy helper retained for callers that still need Thursday expiries."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 3) % 7
    return last_day - timedelta(days=offset)


def next_expiry(from_date: date, weekly: bool = False) -> date:
    """
    Return the next NSE index option expiry date from a given date.

    weekly=True uses the next Tuesday. weekly=False uses the last Tuesday of
    the current or next month.
    """
    if weekly:
        days_until_tuesday = (1 - from_date.weekday()) % 7
        if days_until_tuesday == 0:
            days_until_tuesday = 7
        return from_date + timedelta(days=days_until_tuesday)

    exp = last_tuesday(from_date.year, from_date.month)
    if exp <= from_date:
        if from_date.month == 12:
            exp = last_tuesday(from_date.year + 1, 1)
        else:
            exp = last_tuesday(from_date.year, from_date.month + 1)
    return exp


def time_to_expiry(from_date: date, expiry: date) -> float:
    """Return time to expiry in years (calendar days / 365)."""
    delta = (expiry - from_date).days
    return max(delta / 365.0, 0.0)


def calculate_transaction_cost(
    premium: float,
    lot_size: int,
    num_lots: int,
    side: str,
) -> float:
    """Calculate total transaction cost for one options order."""
    turnover = premium * lot_size * num_lots

    brokerage = min(settings.BROKERAGE_PER_ORDER, turnover * settings.BROKERAGE_MAX_PCT / 100)
    stt_rate = settings.STT_SELL_PCT if side == "sell" else settings.STT_BUY_PCT
    stt = turnover * stt_rate / 100
    exchange = turnover * settings.EXCHANGE_CHARGE_PCT / 100
    gst = (brokerage + exchange) * settings.GST_PCT / 100
    sebi = (turnover / 1e7) * settings.SEBI_CHARGE_PER_CR
    stamp = (turnover * settings.STAMP_DUTY_BUY_PCT / 100) if side == "buy" else 0.0

    total = brokerage + stt + exchange + gst + sebi + stamp
    return round(total, 2)
