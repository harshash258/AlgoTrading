from __future__ import annotations

import glob
import math
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

import numpy as np
import pandas as pd

from nifty_options_algo_v2 import (
    BlackScholes,
    Greeks,
    StrategyParams,
    add_indicators,
    entry_signal,
)

# ============================================================
# 1. MULTI-LEG POSITIONS
# ============================================================

class Structure(Enum):
    LONG_CALL = "Long Call"
    LONG_PUT = "Long Put"
    BULL_CALL_SPREAD = "Bull Call Spread"
    BEAR_PUT_SPREAD = "Bear Put Spread"

    @property
    def is_spread(self) -> bool:
        return self in (
            Structure.BULL_CALL_SPREAD,
            Structure.BEAR_PUT_SPREAD,
        )

    @property
    def is_bullish(self) -> bool:
        return self in (
            Structure.LONG_CALL,
            Structure.BULL_CALL_SPREAD,
        )


@dataclass
class Leg:
    strike: float
    kind: str      # "call" / "put"
    qty: int       # +1 long, -1 short

    def value(self, spot, T, iv, r, q) -> float:
        return self.qty * BlackScholes.price(
            spot,
            self.strike,
            T,
            iv,
            r,
            q,
            self.kind,
        )

    def greeks(self, spot, T, iv, r, q) -> Greeks:
        g = BlackScholes.greeks(
            spot,
            self.strike,
            T,
            iv,
            r,
            q,
            self.kind,
        )
        s = self.qty
        return Greeks(
            s * g.price,
            s * g.delta,
            s * g.gamma,
            s * g.theta,
            s * g.vega,
            s * g.rho,
        )


@dataclass
class Position:
    structure: Structure
    legs: list[Leg]

    def net_value(self, spot, T, iv, r=0.065, q=0.012) -> float:
        """Net debit/credit. Positive = you paid (debit)."""
        return sum(
            l.value(spot, T, iv, r, q)
            for l in self.legs
        )

    def net_greeks(self, spot, T, iv, r=0.065, q=0.012) -> Greeks:
        gs = [l.greeks(spot, T, iv, r, q) for l in self.legs]
        return Greeks(*[
            sum(getattr(g, f) for g in gs)
            for f in ("price", "delta", "gamma", "theta", "vega", "rho")
        ])

    def payoff_at_expiry(self, spot) -> float:
        tot = 0.0
        for l in self.legs:
            intrinsic = (
                max(spot - l.strike, 0)
                if l.kind == "call"
                else max(l.strike - spot, 0)
            )
            tot += l.qty * intrinsic
        return tot

    @property
    def width(self) -> float:
        """Strike width for a spread, else inf."""
        if not self.structure.is_spread:
            return float("inf")
        return abs(self.legs[0].strike - self.legs[1].strike)

    def max_return_pct(self, debit: float) -> float:
        """Best achievable % return. Spreads are capped; singles are not."""
        if not self.structure.is_spread or debit <= 0:
            return float("inf")
        return (self.width - debit) / debit * 100.0

    def describe(self) -> str:
        parts = []
        for l in sorted(self.legs, key=lambda x: x.strike):
            side = "+" if l.qty > 0 else "-"
            parts.append(f"{side}{l.strike:.0f}{l.kind[0].upper()}")
        return f"{self.structure.value} ({', '.join(parts)})"


def build_position(
    structure: Structure,
    spot: float,
    otm_pct: float,
    spread_width_pct: float = 2.0,
    step: int = 50,
) -> Position:
    """
    Construct a position from spot and % offsets.
    Strikes snap to the 50 grid.
    """

    def snap(x):
        return round(x / step) * step

    if structure is Structure.LONG_CALL:
        return Position(
            structure,
            [Leg(snap(spot * (1 + otm_pct / 100)), "call", +1)],
        )

    if structure is Structure.LONG_PUT:
        return Position(
            structure,
            [Leg(snap(spot * (1 - otm_pct / 100)), "put", +1)],
        )

    if structure is Structure.BULL_CALL_SPREAD:
        lo = snap(spot * (1 + otm_pct / 100))
        hi = snap(spot * (1 + (otm_pct + spread_width_pct) / 100))
        if hi <= lo:
            hi = lo + step
        return Position(
            structure,
            [Leg(lo, "call", +1), Leg(hi, "call", -1)]
        )

    if structure is Structure.BEAR_PUT_SPREAD:
        hi = snap(spot * (1 - otm_pct / 100))
        lo = snap(spot * (1 - (otm_pct + spread_width_pct) / 100))
        if lo >= hi:
            lo = hi - step
        return Position(
            structure,
            [Leg(hi, "put", +1), Leg(lo, "put", -1)]
        )

    raise ValueError(structure)


# ============================================================
# 2. SPREAD-AWARE PARAMS + BACKTEST
# ============================================================

@dataclass
class SpreadParams(StrategyParams):
    bull_structure: Structure = Structure.BULL_CALL_SPREAD
    bear_structure: Structure = Structure.BEAR_PUT_SPREAD
    spread_width_pct: float = 2.0
    clamp_target_to_max: bool = True      # spreads cap out; don't chase impossible targets
    exit_on_trend_break: bool = False     # v2 finding: conflicts with MA-cross entry


@dataclass
class SpreadTrade:
    entry_date: pd.Timestamp
    position: Position
    spot_entry: float
    expiry_date: pd.Timestamp
    entry_debit: float
    entry_iv: float
    entry_greeks: Greeks
    effective_target: float
    max_return_pct: float
    peak_value: float = 0.0
    exit_date: pd.Timestamp | None = None
    spot_exit: float = 0.0
    exit_value: float = 0.0
    exit_reason: str = ""
    days_held: int = 0

    @property
    def pnl_pct(self) -> float:
        if self.entry_debit <= 0:
            return 0.0
        return (
            (self.exit_value - self.entry_debit)
            / self.entry_debit
            * 100.0
        )


class SpreadBacktest:

    def __init__(self, df: pd.DataFrame, params: SpreadParams):
        self.p = params
        self.df = add_indicators(df, params)
        self.trades: list[SpreadTrade] = []

    def run(self) -> list[SpreadTrade]:
        p, df = self.p, self.df
        self.trades = []

        open_t: SpreadTrade | None = None
        warmup = max(p.ma_period, p.rsi_period, 20) + 5

        for i in range(warmup, len(df)):
            date, row = df.index[i], df.iloc[i]
            spot = float(row["Close"])
            iv = float(row["IV"])

            # ---------- manage ----------
            if open_t is not None:
                t = open_t
                dte_days = (t.expiry_date - date).days
                T = max(dte_days, 0) / 365.0

                val = t.position.net_value(
                    spot,
                    T,
                    iv,
                    p.risk_free_rate,
                    p.dividend_yield,
                )

                t.peak_value = max(t.peak_value, val)

                ret = (val - t.entry_debit) / t.entry_debit
                held = (date - t.entry_date).days

                reason = None

                if ret >= t.effective_target:
                    reason = "Profit target"

                elif ret <= -p.stop_loss:
                    reason = "Stop loss"

                elif (
                    p.trailing_stop > 0
                    and t.peak_value >= (
                        t.entry_debit
                        * (1 + p.trailing_activate)
                    )
                    and val <= (
                        t.peak_value
                        * (1 - p.trailing_stop)
                    )
                ):
                    reason = "Trailing stop"

                elif (
                    p.atr_stop_multiple > 0
                    and not pd.isna(row["ATR"])
                ):
                    adverse = (
                        (t.spot_entry - spot)
                        if t.position.structure.is_bullish
                        else (spot - t.spot_entry)
                    )

                    if adverse >= (
                        p.atr_stop_multiple
                        * float(row["ATR"])
                    ):
                        reason = "ATR volatility stop"

                if reason is None and dte_days <= p.min_days_to_expiry:
                    reason = "Time decay"

                if reason is None and held >= p.max_hold_days:
                    reason = "Max hold"

                if reason is None and p.exit_on_trend_break:
                    broke = (
                        (spot < row["MA"])
                        if t.position.structure.is_bullish
                        else (spot > row["MA"])
                    )
                    if broke:
                        reason = "Trend break"

                if reason:
                    t.exit_date = date
                    t.spot_exit = spot
                    t.days_held = held
                    t.exit_value = val * (1 - p.slippage_percent / 2)
                    t.exit_reason = reason
                    self.trades.append(t)
                    open_t = None

            # ---------- enter ----------
            if open_t is None:
                sig = entry_signal(df, i, p)

                if sig != 0:
                    struct = (
                        p.bull_structure
                        if sig == 1
                        else p.bear_structure
                    )

                    pos = build_position(
                        struct,
                        spot,
                        p.otm_percent,
                        p.spread_width_pct,
                    )

                    T = p.days_to_expiry / 365.0

                    debit = pos.net_value(
                        spot,
                        T,
                        iv,
                        p.risk_free_rate,
                        p.dividend_yield,
                    )

                    if debit <= 0.5:
                        continue

                    debit *= (1 + p.slippage_percent / 2)

                    max_ret = pos.max_return_pct(debit)
                    tgt = p.profit_target

                    if (
                        p.clamp_target_to_max
                        and max_ret != float("inf")
                    ):
                        tgt = min(
                            tgt,
                            max_ret / 100.0 * 0.90,   # 90% of theoretical max
                        )

                    open_t = SpreadTrade(
                        entry_date=date,
                        position=pos,
                        spot_entry=spot,
                        expiry_date=date + timedelta(days=p.days_to_expiry),
                        entry_debit=debit,
                        entry_iv=iv,
                        entry_greeks=pos.net_greeks(
                            spot,
                            T,
                            iv,
                            p.risk_free_rate,
                            p.dividend_yield,
                        ),
                        effective_target=tgt,
                        max_return_pct=max_ret,
                        peak_value=debit,
                    )

        if open_t is not None:
            date = df.index[-1]
            spot = float(df["Close"].iloc[-1])
            iv = float(df["IV"].iloc[-1])

            T = max(
                (open_t.expiry_date - date).days,
                0,
            ) / 365.0

            t = open_t
            t.exit_date = date
            t.spot_exit = spot
            t.exit_value = t.position.net_value(
                spot,
                T,
                iv,
                self.p.risk_free_rate,
                self.p.dividend_yield,
            )
            t.exit_reason = "End of data"
            t.days_held = (date - t.entry_date).days
            self.trades.append(t)

        return self.trades

    def stats(self) -> dict:
        if not self.trades:
            return {"trades": 0}

        r = np.array([t.pnl_pct for t in self.trades])
        wins, losses = r[r > 0], r[r <= 0]

        eq = np.cumprod(1 + r / 100.0)
        dd = float((eq / np.maximum.accumulate(eq) - 1).min() * 100)
        gl = abs(losses.sum()) if len(losses) else 0.0

        return {
            "trades": len(r),
            "win_rate": float(len(wins) / len(r) * 100),
            "total_return_pct": float((eq[-1] - 1) * 100),
            "avg_trade_pct": float(r.mean()),
            "avg_win_pct": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss_pct": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": float(wins.sum() / gl) if gl > 0 else float("inf"),
            "sharpe_per_trade": float(r.mean() / r.std()) if r.std() > 0 else 0.0,
            "max_drawdown_pct": dd,
            "avg_days_held": float(np.mean([t.days_held for t in self.trades])),
            "avg_debit": float(np.mean([t.entry_debit for t in self.trades])),
            "avg_theta_pct": float(
                np.mean([
                    t.entry_greeks.theta / t.entry_debit * 100
                    for t in self.trades
                ])
            ),
            "avg_delta": float(
                np.mean([
                    t.entry_greeks.delta
                    for t in self.trades
                ])
            ),
            "avg_max_return_cap": float(
                np.mean([
                    min(t.max_return_pct, 9999)
                    for t in self.trades
                ])
            ),
        }


def compare_structures(
    df: pd.DataFrame,
    base: SpreadParams = None,
) -> pd.DataFrame:
    """
    Same entry signals, four different structures.
    This is the real comparison.
    """
    base = base or SpreadParams()

    combos = [
        ("Long Call / Long Put", Structure.LONG_CALL, Structure.LONG_PUT),
        ("Spread 1% Wide", Structure.BULL_CALL_SPREAD, Structure.BEAR_PUT_SPREAD),
        ("Spread 2% Wide", Structure.BULL_CALL_SPREAD, Structure.BEAR_PUT_SPREAD),
        ("Spread 4% Wide", Structure.BULL_CALL_SPREAD, Structure.BEAR_PUT_SPREAD),
    ]

    widths = [None, 1.0, 2.0, 4.0]
    rows = []

    for (label, bull, bear), w in zip(combos, widths):
        kw = dict(bull_structure=bull, bear_structure=bear)
        if w is not None:
            kw["spread_width_pct"] = w

        p = SpreadParams(**{**base.__dict__, **kw})
        bt = SpreadBacktest(df, p)
        bt.run()
        s = bt.stats()

        if s.get("trades", 0) == 0:
            continue

        rows.append({
            "structure": label,
            "trades": s["trades"],
            "win%": round(s["win_rate"], 1),
            "avg%": round(s["avg_trade_pct"], 1),
            "ret%": round(s["total_return_pct"], 1),
            "PF": round(s["profit_factor"], 2),
            "sharpe": round(s["sharpe_per_trade"], 3),
            "maxDD%": round(s["max_drawdown_pct"], 1),
            "debit": round(s["avg_debit"], 1),
            "theta%/d": round(s["avg_theta_pct"], 2),
            "delta": round(s["avg_delta"], 3),
            "cap%": round(s["avg_max_return_cap"], 0),
        })

    out = pd.DataFrame(rows)

    print("\nSTRUCTURE COMPARISON (identical entry signals)")
    print(out.to_string(index=False))
    print("\n theta%/d = daily premium bleed as % of debit (less negative is better)")
    print(" cap% = max achievable return; spreads are capped, singles are not")

    return out


# ============================================================
# 3. NSE BHAVCOPY OPTION CHAIN LOADER
# ============================================================

# Maps canonical name -> possible column names across NSE schema versions
COLUMN_ALIASES = {
    "symbol": ["SYMBOL", "TckrSymb", "TICKER"],
    "instrument": ["INSTRUMENT", "FinInstrmTp", "INSTRUMENT_TYPE"],
    "expiry": ["EXPIRY_DT", "XpryDt", "EXPIRY"],
        "strike": ["STRIKE_PR", "StrkPric", "STRIKE_PRICE"],
    "opt_type": ["OPTION_TYP", "OptnTp", "OPTION_TYPE"],
    "open": ["OPEN", "OpnPric"],
    "high": ["HIGH", "HghPric"],
    "low": ["LOW", "LwPric"],
    "close": ["CLOSE", "ClsPric"],
    "settle": ["SETTLE_PR", "SttlmPric"],
    "oi": ["OPEN_INT", "OpnIntrst"],
    "volume": ["CONTRACTS", "TtlTradgVol", "TtlTrfVal"],
    "date": ["TIMESTAMP", "TradDt", "DATE"],
}


def _map_columns(cols) -> dict:
    upper = {str(c).strip(): str(c).strip() for c in cols}
    found = {}

    for canon, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            for c in upper:
                if c.upper() == a.upper():
                    found[canon] = c
                    break
            if canon in found:
                break

    return found


def load_bhavcopy(
    path: str,
    symbol: str = "NIFTY",
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Load one bhavcopy file (.csv or .csv.zip) and return normalized option rows.

    Returns columns:
        date, expiry, strike, opt_type ('CE'/'PE'), close,
        settle, oi, volume
    """

    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            with z.open(name) as fh:
                raw = pd.read_csv(fh, low_memory=False)
    else:
        raw = pd.read_csv(path, low_memory=False)

    raw.columns = [str(c).strip() for c in raw.columns]

    m = _map_columns(raw.columns)

    required = ("symbol", "expiry", "strike", "opt_type", "close")
    missing = [k for k in required if k not in m]

    if missing:
        raise ValueError(
            f"Could not map required columns {missing} in {os.path.basename(path)}.\n"
            f"Columns present: {list(raw.columns)}\n"
            f"Add the right names to COLUMN_ALIASES and re-run."
        )

    if verbose:
        print(f" column map: {m}")

    df = raw.rename(columns={v: k for k, v in m.items()})

    df = df[df["symbol"].astype(str).str.upper().str.strip() == symbol.upper()]

    # The CE/PE filter below is the reliable option test. The instrument column
    # only EXCLUDES known futures markers, because option markers differ by
    # schema version (legacy OPTIDX/OPTSTK vs UDIFF IDO/STO) and requiring
    # "OPT" silently drops every UDIFF row.
    if "instrument" in df:
        fut = df["instrument"].astype(str).str.upper().str.strip()
        df = df[
            ~fut.isin({
                "FUTIDX",
                "FUTSTK",
                "FUTIVX",
                "FUTCUR",
                "FUTIRT",
                "IDF",
                "STF",
                "CRF",
                "IRF",
            })
        ]

    df = df[
        df["opt_type"]
        .astype(str)
        .str.upper()
        .str.strip()
        .isin(["CE", "PE"])
    ]

    df["expiry"] = pd.to_datetime(
        df["expiry"],
        errors="coerce",
        dayfirst=True,
    )

    if "date" in df:
        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce",
            dayfirst=True,
        )
    else:
        df["date"] = pd.NaT

    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    for c in ("close", "settle", "oi", "volume", "open", "high", "low"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    keep = [
        c
        for c in [
            "date",
            "expiry",
            "strike",
            "opt_type",
            "close",
            "settle",
            "oi",
            "volume",
        ]
        if c in df
    ]

    out = df[keep].dropna(subset=["expiry", "strike", "close"])
    out = out[out["close"] > 0]
    return out.reset_index(drop=True)


def load_bhavcopy_folder(
    folder: str,
    symbol: str = "NIFTY",
    pattern: str = "*.zip",
) -> pd.DataFrame:
    """
    Load and concatenate every bhavcopy in a folder into one option chain table.
    """

    files = sorted(glob.glob(os.path.join(folder, pattern)))

    if not files:
        raise FileNotFoundError(
            f"No files matching {pattern} in {folder}"
        )

    print(f"Loading {len(files)} bhavcopy files from {folder}...")

    frames, failed = [], []

    for n, f in enumerate(files):
        try:
            frames.append(
                load_bhavcopy(
                    f,
                    symbol,
                    verbose=(n == 0),
                )
            )
        except Exception as e:
            failed.append(
                (os.path.basename(f), str(e)[:90])
            )

    if failed:
        print(f"{len(failed)} file(s) failed!")
        for name, err in failed[:5]:
            print(f"  {name}: {err}")

    if not frames:
        raise RuntimeError("All files failed to parse.")

    chain = pd.concat(frames, ignore_index=True)
    chain = chain.sort_values(["date", "expiry", "strike", "opt_type"])

    print(
        f" Loaded {len(chain):,} option rows | "
        f"{chain['date'].min()} to {chain['date'].max()} | "
        f"{chain['expiry'].nunique()} expiries | "
        f"{chain['strike'].nunique()} strikes"
    )

    return chain.reset_index(drop=True)


class ChainLookup:
    """Fast (date, expiry, strike, type) -> premium lookup over a bhavcopy chain."""

    def __init__(self, chain: pd.DataFrame, price_col: str = "close"):
        if price_col not in chain.columns:
            price_col = "settle" if "settle" in chain.columns else "close"

        self.price_col = price_col
        self._idx = chain.set_index(
            ["date", "expiry", "strike", "opt_type"]
        )[price_col]
        self._idx = self._idx[
            ~self._idx.index.duplicated(keep="last")
        ].sort_index()

        self._dates = np.array(sorted(chain["date"].dropna().unique()))
        self._expiries = np.array(sorted(chain["expiry"].dropna().unique()))
        self._strikes = np.array(sorted(chain["strike"].dropna().unique()))

    def nearest_expiry(
        self,
        date,
        min_days: int,
    ) -> pd.Timestamp | None:
        target = pd.Timestamp(date) + pd.Timedelta(days=min_days)
        later = self._expiries[self._expiries >= np.datetime64(target)]
        return pd.Timestamp(later[0]) if len(later) else None

    def nearest_strike(self, strike: float) -> float:
        return float(
            self._strikes[
                np.abs(self._strikes - strike).argmin()
            ]
        )

    def premium(
        self,
        date,
        expiry,
        strike,
        opt_type,
    ) -> float | None:
        try:
            v = self._idx.loc[
                (
                    pd.Timestamp(date),
                    pd.Timestamp(expiry),
                    float(strike),
                    opt_type,
                )
            ]
            return float(v)
        except KeyError:
            return None

    def implied_vol(
        self,
        date,
        expiry,
        strike,
        opt_type,
        spot,
        r=0.065,
        q=0.012,
    ) -> float | None:

        px = self.premium(date, expiry, strike, opt_type)
        if px is None:
            return None

        T = (pd.Timestamp(expiry) - pd.Timestamp(date)).days / 365.0
        kind = "call" if opt_type == "CE" else "put"

        return BlackScholes.implied_vol(px, spot, strike, T, r, q, kind)

    def smile(
        self,
        date,
        expiry,
        spot,
        r=0.065,
        q=0.012,
    ) -> pd.DataFrame:
        """
        IV by strike for one date/expiry — sanity-check your data with this.
        """

        rows = []

        for opt_type, kind in (("CE", "call"), ("PE", "put")):
            for k in self.strikes:
                px = self.premium(date, expiry, k, opt_type)
                if px is None or px <= 0:
                    continue

                T = (pd.Timestamp(expiry) - pd.Timestamp(date)).days / 365.0
                iv = BlackScholes.implied_vol(px, spot, k, T, r, q, kind)

                rows.append({
                    "strike": k,
                    "type": opt_type,
                    "premium": px,
                    "iv_pct": round(iv * 100, 2) if iv else None,
                    "moneyness": round(k / spot, 4),
                })

        return pd.DataFrame(rows).sort_values(["type", "strike"])


def price_position_from_chain(
    pos: Position,
    lookup: ChainLookup,
    date,
    expiry,
) -> float | None:
    """
    Net debit of a Position using REAL bhavcopy premiums.
    None if any leg missing.
    """

    total = 0.0

    for l in pos.legs:
        ot = "CE" if l.kind == "call" else "PE"
        px = lookup.premium(
            date,
            expiry,
            lookup.nearest_strike(l.strike),
            ot,
        )

        if px is None:
            return None

        total += l.qty * px

    return total


# ==========================================================
# 4. DEMO / ENTRY POINT
# ==========================================================

def demo_structures_on_index(
    symbol="NSEI",
    start="2018-01-01",
):
    from nifty_options_algo_v2 import load

    df = load(symbol, start=start)

    print(
        f"Loaded {len(df)} bars: "
        f"{df.index[0].date()} to {df.index[-1].date()}"
    )

    compare_structures(df)


def demo_bhavcopy(folder: str, symbol="NIFTY"):
    chain = load_bhavcopy_folder(folder, symbol)

    d = chain["date"].dropna().iloc[len(chain) // 2]
    exp = chain[(chain["date"] == d)]["expiry"].min()
    atm = chain[(chain["date"] == d) & (chain["expiry"] == exp)]

    spot_guess = atm.loc[
        (atm["strike"] - atm["strike"].median()).abs().idxmin(),
        "strike"
    ]

    print(
        f"\nSample IV smile - {d.date()} exp {pd.Timestamp(exp).date()} "
        f"(spot approximated as {spot_guess:.0f}):"
    )

    lk = ChainLookup(chain)
    sm = lk.smile(d, exp, spot_guess)

    print(
        sm[
            (sm["moneyness"] > 0.94) &
            (sm["moneyness"] < 1.06)
        ].to_string(index=False)
    )

    print(
        "\n A U-shaped IV curve means your data is sane. "
        "A flat or ragged curve means bad rows, stale prints, "
        "or a wrong spot."
    )

    return chain


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        demo_bhavcopy(sys.argv[1])
    else:
        demo_structures_on_index("NSEI")
        print(
            "\nTo use real option data: "
            "python nifty_options_algo_v3.py /path/to/bhavcopy_folder"
        )