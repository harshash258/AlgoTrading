"""Market regime classification helpers for reports and optimization."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RegimeSnapshot:
    primary: str
    tags: tuple[str, ...]


def classify_regime(data: pd.DataFrame, vix_window: int = 60) -> RegimeSnapshot:
    """Classify the latest row into practical strategy-review buckets."""
    if data.empty or "Close" not in data:
        return RegimeSnapshot("unknown", ("unknown",))

    close = data["Close"].dropna()
    tags: list[str] = []

    if len(close) >= 50:
        fast = close.rolling(20).mean().iloc[-1]
        slow = close.rolling(50).mean().iloc[-1]
        trend_gap = abs(fast - slow) / close.iloc[-1] * 100 if close.iloc[-1] else 0
        tags.append("trend" if trend_gap >= 1.0 else "chop/range")
    else:
        tags.append("chop/range")

    if "VIX" in data and len(data["VIX"].dropna()) >= 5:
        vix = data["VIX"].dropna()
        current = float(vix.iloc[-1])
        window = vix.iloc[-vix_window:]
        pct = (window < current).sum() / len(window) * 100
        if pct >= 70:
            tags.append("high IV")
        elif pct <= 30:
            tags.append("low IV")

    if len(data) >= 2 and {"Open", "Close"}.issubset(data.columns):
        prev_close = float(data["Close"].iloc[-2])
        today_open = float(data["Open"].iloc[-1])
        if prev_close and abs((today_open - prev_close) / prev_close * 100) >= 0.5:
            tags.append("gap")

    return RegimeSnapshot(tags[0], tuple(dict.fromkeys(tags)))


def regime_for_trade(trade, data_by_underlying: dict[str, pd.DataFrame]) -> str:
    """Return the primary regime on a trade's signal date when data is available."""
    df = data_by_underlying.get(trade.underlying)
    signal_date = getattr(trade, "signal_date", None) or getattr(trade, "entry_date", None)
    if df is None or signal_date is None:
        return "unknown"
    sliced = df.loc[df.index.date <= signal_date]
    return classify_regime(sliced).primary


def summarize_regime_performance(trades_df: pd.DataFrame, min_trades: int) -> pd.DataFrame:
    """Rank best/worst strategy per regime with minimum-sample warnings."""
    if trades_df.empty or "Regime" not in trades_df:
        return pd.DataFrame()
    rows = []
    for (regime, strategy), group in trades_df.groupby(["Regime", "Strategy"]):
        n = len(group)
        if "group_id" in group:
            keys = group["group_id"].where(group["group_id"].notna() & group["group_id"].ne(""), group.index.astype(str))
            n = keys.nunique()
        net_pnl = float(group["P&L ₹"].sum()) if "P&L ₹" in group else float(group["net_pnl"].sum())
        rows.append({
            "Regime": regime,
            "Strategy": strategy,
            "Trades": n,
            "Net P&L": round(net_pnl, 2),
            "Avg P&L": round(net_pnl / n, 2) if n else 0.0,
            "Sample": "ok" if n >= min_trades else "insufficient",
        })
    return pd.DataFrame(rows).sort_values(["Regime", "Avg P&L"], ascending=[True, False])
