"""Effective-dated charges from an operator-supplied, sourced CSV."""
from functools import lru_cache
from types import SimpleNamespace
import math
import pandas as pd

RATE_FIELDS = ("BROKERAGE_PER_ORDER", "BROKERAGE_MAX_PCT", "STT_SELL_PCT", "STT_BUY_PCT",
               "EXCHANGE_CHARGE_PCT", "GST_PCT", "SEBI_CHARGE_PER_CR", "STAMP_DUTY_BUY_PCT")


@lru_cache(maxsize=8)
def _load(path):
    rows = pd.read_csv(path).to_dict("records")
    for row in rows:
        row["effective_from"] = pd.Timestamp(row["effective_from"]).date()
        row["effective_to"] = pd.Timestamp(row["effective_to"]).date()
        for key in RATE_FIELDS:
            row[key] = float(row[key])
            if not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError(f"Invalid fee rate {key}")
    return rows


def fee_rates(path, when):
    matches = [r for r in _load(str(path)) if r["effective_from"] <= when <= r["effective_to"]]
    if len(matches) != 1:
        raise ValueError(f"Missing or overlapping fee schedule for {when}")
    return SimpleNamespace(**matches[0])
