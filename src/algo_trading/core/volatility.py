"""Point-in-time contract IV comparisons; India VIX is deliberately absent."""
import math
import pandas as pd


def expiry_days(expiry, entry_at):
    entry_at = pd.Timestamp(entry_at)
    if entry_at.tzinfo is None:
        raise ValueError("Entry timestamp requires timezone")
    close = pd.Timestamp(str(expiry) + " 15:30", tz="Asia/Kolkata")
    return (close - entry_at).total_seconds() / 86400


def tenor_bucket(days):
    return "expiry_day" if days < 1 else "1-7d" if days <= 7 else "8-30d" if days <= 30 else "31d+"


def contract_iv_context(iv, history, underlying, kind, days, moneyness, asof, minimum=20):
    result = {"iv": iv, "percentile": None, "samples": 0, "tenor": tenor_bucket(days),
              "source": "option_premium_inversion", "reason": "insufficient comparable IV history"}
    if iv is None or not math.isfinite(iv) or iv <= 0:
        result.update(iv=None, reason="contract IV unavailable")
        return result
    if history is None or history.empty:
        return result
    h = history.copy()
    h["timestamp"] = pd.to_datetime(h.timestamp, utc=True)
    # Compare earlier sessions only, same underlying/type/tenor and near moneyness.
    cutoff = pd.Timestamp(asof)
    if cutoff.tzinfo is None:
        raise ValueError("IV cutoff requires timezone")
    h = h.loc[(h.timestamp < cutoff.normalize()) & (h.underlying == underlying) &
              (h.option_type == kind) & (h.dte.map(tenor_bucket) == tenor_bucket(days)) &
              ((h.moneyness - moneyness).abs() <= .02) &
              h.iv.map(lambda v: math.isfinite(v) and 0 < v <= 5)]
    # One daily median avoids inflating sample counts with ticks or nearby strikes.
    values = h.groupby(h.timestamp.dt.tz_convert("Asia/Kolkata").dt.date).iv.median().tail(252)
    result["samples"] = len(values)
    if len(values) >= minimum:
        result.update(percentile=float((values < iv).mean() * 100), reason="")
    return result
