"""Two-stage daily decisions, using existing strategies and execution primitives.

No message transport or order API is called here. EOD prices are estimates only.
"""
from dataclasses import dataclass, asdict
from types import SimpleNamespace
from collections import defaultdict
from datetime import time
from pathlib import Path
import json
import math
import pandas as pd
from config import settings
from algo_trading.core.contracts import ContractMaster
from algo_trading.core.pricing import implied_volatility, calculate_transaction_cost
from algo_trading.core.risk_manager import RiskManager
from algo_trading.core.structures import validate_structure, expiry_risk
from algo_trading.core.volatility import expiry_days, contract_iv_context
from algo_trading.data.intraday import session_signals


@dataclass(frozen=True)
class QualityPolicy:
    allow_expiry_day: bool = False
    expiry_day_cutoff: str = "12:00"
    min_hours_to_expiry: float = 2
    max_quote_age_seconds: int = 30
    max_bar_age_seconds: int = 90
    max_spread_pct: float = 5
    min_iv_sessions: int = 20
    low_iv_percentile: float = 30
    high_iv_percentile: float = 70

    def __post_init__(self):
        time.fromisoformat(self.expiry_day_cutoff)
        for value in (self.min_hours_to_expiry, self.max_quote_age_seconds, self.max_bar_age_seconds,
                      self.max_spread_pct, self.min_iv_sessions):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Quality limits must be positive and finite")
        if not 0 <= self.low_iv_percentile <= self.high_iv_percentile <= 100:
            raise ValueError("Invalid IV percentile thresholds")


def contract_key(ticker, leg):
    return f"{ticker}|{leg['expiry']}|{float(leg['strike']):g}|{leg['option_type']}"


def collect_watchlist(data, asof, entry_at=None, policy=None):
    """Use EOD strategies for context; ORB/VWAP are conditional session plans."""
    from algo_trading.notifications import telegram as legacy
    policy = policy or QualityPolicy()
    def eligible(expiry):
        if entry_at is None:
            return True
        ts = pd.Timestamp(entry_at).tz_convert("Asia/Kolkata")
        return (expiry_days(expiry, ts) * 24 >= policy.min_hours_to_expiry and
                (expiry != ts.date() or (policy.allow_expiry_day and ts.time() < time.fromisoformat(policy.expiry_day_cutoff))))
    collected, reasons = legacy._collect_signals(data, asof, quality_mode=True, expiry_filter=eligible)
    # Real intraday strategies have no daily entry signal. Prepare both directions.
    for ticker, frame in data.items():
        if frame.empty or frame.index.max().date() != asof:
            continue
        copy = frame.copy()
        if not legacy._set_live_expiries(copy, ticker, asof):
            continue
        copy.attrs["contract_expiries"] = [e for e in copy.attrs["contract_expiries"] if eligible(e)]
        if not copy.attrs["contract_expiries"]:
            continue
        spot = float(frame.Close.iloc[-1])
        atm = round(spot / settings.STRIKE_STEPS[ticker]) * settings.STRIKE_STEPS[ticker]
        item = collected.setdefault(ticker, {"spot": spot, "atm": atm, "name": ticker, "sigs": []})
        for strategy in ("ORB", "Session VWAP"):
            for kind in ("CE", "PE"):
                item["sigs"].append(dict(strategy_label=strategy, direction="long", option_type=kind,
                    expiry=copy.attrs["contract_expiries"][0], strike=atm, structure_type="single", meta={}))
    collected, blocked = legacy._validate_collected_signals(data, collected, asof)
    return collected, reasons + blocked


def _trigger(provider, ticker, legs, label, entry_at, policy, eod_spot):
    if provider is None:
        return False, "intraday candles and fresh option quotes unavailable", None
    bars = provider.candles(ticker, entry_at)
    if bars.empty:
        return False, "intraday candles unavailable", None
    bars.index = bars.index.tz_convert("Asia/Kolkata")
    bars = bars.loc[(bars.index <= entry_at) & (bars.index.date == entry_at.date())]
    bars = bars.between_time("09:15", "15:30", inclusive="right")
    if bars.empty or (entry_at - bars.index[-1]).total_seconds() > policy.max_bar_age_seconds:
        return False, "completed intraday candle is stale or missing", None
    if label in {"ORB", "Session VWAP"}:
        # Require a complete one-minute session prefix; gaps cannot manufacture ORB/VWAP.
        expected = pd.date_range(pd.Timestamp(str(entry_at.date()) + " 09:16", tz="Asia/Kolkata"), bars.index[-1], freq="min")
        if not bars.index.equals(expected):
            return False, "complete one-minute session candles required", None
        if label == "Session VWAP" and (bars.Volume <= 0).any():
            return False, "VWAP requires actual traded volume (index volume is not a substitute)", None
        signals = session_signals(bars, "orb" if label == "ORB" else "vwap")
        # Execute only after a completed trigger bar, never at its closing price.
        prior = signals.loc[signals.index < entry_at]
        direction = 1 if legs[0]["option_type"] == "CE" else -1
        fired = not prior.empty and prior.iloc[-1] == direction and (entry_at - prior.index[-1]).total_seconds() <= policy.max_bar_age_seconds
    elif label in {"Long Straddle (Low IV)", "Long Strangle (Low IV)", "Short Strangle (High IV)", "Iron Condor"}:
        fired = True  # Volatility threshold is checked independently for every live leg.
    else:
        direction = 1 if legs[0]["option_type"] == "CE" else -1
        fired = (float(bars.Close.iloc[-1]) - eod_spot) * direction > 0
    return bool(fired), "trigger observed" if fired else "awaiting completed-candle trigger", float(bars.Close.iloc[-1])


def build_daily_report(collected, asof, entry_at, master=None, provider=None, history=None,
                       portfolio=None, policy=None, reasons=()):
    policy, master = policy or QualityPolicy(), master or ContractMaster()
    entry_at = pd.Timestamp(entry_at)
    if entry_at.tzinfo is None:
        raise ValueError("--entry-at must include timezone")
    entry_at = entry_at.tz_convert("Asia/Kolkata")
    if entry_at.date() <= asof:
        raise ValueError("Confirmation must follow the EOD observation session")
    portfolio = portfolio or {}
    equity = float(portfolio.get("equity", settings.STARTING_CAPITAL))
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("Positive finite portfolio equity required")
    rm = RiskManager(equity)
    rm.peak_capital = float(portfolio.get("peak_equity", equity))
    rm.day_start_equity = float(portfolio.get("day_start_equity", equity))
    if not all(math.isfinite(v) and v > 0 for v in (rm.peak_capital, rm.day_start_equity)):
        raise ValueError("Invalid portfolio equity history")
    exposure = defaultdict(int)
    for i, position in enumerate(portfolio.get("positions", [])):
        capital, risk = float(position["reserved_capital"]), float(position["max_loss"])
        if not all(math.isfinite(x) and x >= 0 for x in (capital, risk)):
            raise ValueError("Invalid existing exposure")
        rm.register_open(f"existing-{i}", capital, risk)
        for leg in position.get("legs", []):
            units = float(leg["units"])
            if not math.isfinite(units) or units != int(units):
                raise ValueError("Existing units must be signed integers")
            exposure[contract_key(position["underlying"], leg)] += int(units)
    snapshot_at = pd.Timestamp(portfolio["asof"]) if portfolio.get("asof") else None
    portfolio_fresh = (snapshot_at is not None and snapshot_at.tzinfo is not None and
                       0 <= (entry_at - snapshot_at).total_seconds() <= 60)
    if "available_cash" in portfolio:
        cash = float(portfolio["available_cash"])
        if not math.isfinite(cash) or cash < 0:
            raise ValueError("Invalid available cash")
        rm.capital = min(equity, cash + sum(v[0] for v in rm.reservations.values()))
    report = {"asof": str(asof), "entry_at": entry_at.isoformat(), "provider": getattr(provider, "name", "unavailable"),
              "policy": asdict(policy), "confirmed_entries": [], "awaiting_triggers": [], "alternatives": [],
              "no_trade_reasons": list(reasons), "ranking": "eligible first, trigger observed, narrowest spread, strategy name; heuristic, not predicted accuracy",
              "sizing_basis": "EOD estimate; recheck at entry" if provider is None else "observed quotes; not an order or guaranteed fill",
              "fee_basis": "effective_dated" if settings.FEE_SCHEDULE_PATH else "current_rates_proxy",
              "portfolio_verified": bool(portfolio.get("verified", False) and portfolio_fresh), "iv_observations": []}
    candidates = []
    for ticker, item in collected.items():
        groups = defaultdict(list)
        for i, leg in enumerate(item["sigs"]):
            key = leg.get("group_id") or (leg["strategy_label"], str(leg["expiry"])) if leg.get("structure_type", "single") != "single" else i
            groups[key].append(leg)
        seen = {}
        for legs in groups.values():
            label = legs[0]["strategy_label"]
            signature = tuple(sorted((contract_key(ticker, l), l["direction"]) for l in legs))
            if signature in seen:
                # Keep distinct trigger rules, but merge identical strategy families/contracts.
                if label not in {"ORB", "Session VWAP"} and seen[signature]["strategy"] not in {"ORB", "Session VWAP"}:
                    seen[signature]["supporting_strategies"].append(label)
                    continue
            setup = {"underlying": ticker, "strategy": label, "supporting_strategies": [label],
                     "legs": [], "reasons": [], "lots": 0, "estimated_lots": 0, "spread_pct": 0.0}
            seen[signature] = setup
            days = expiry_days(legs[0]["expiry"], entry_at)
            setup["entry_dte"] = days
            setup["confirmation_rule"] = ("Completed one-minute opening-range breakout" if label == "ORB" else
                "Completed session VWAP crossing with actual traded volume" if label == "Session VWAP" else
                "Contract IV threshold on every leg and fresh intraday spot" if label in {"Long Straddle (Low IV)", "Long Strangle (Low IV)", "Short Strangle (High IV)", "Iron Condor"} else
                "Completed intraday close beyond EOD close in the setup direction")
            setup["expiry_day"] = legs[0]["expiry"] == entry_at.date()
            if days <= 0 or days * 24 < policy.min_hours_to_expiry:
                setup["reasons"].append("insufficient time to listed expiry at entry")
            if setup["expiry_day"] and (not policy.allow_expiry_day or entry_at.time() >= time.fromisoformat(policy.expiry_day_cutoff)):
                setup["reasons"].append("expiry-day entry disabled or past cutoff")
            try:
                fired, trigger_reason, live_spot = _trigger(provider, ticker, legs, label, entry_at, policy, item["spot"])
                quotes = provider.quotes(ticker, entry_at) if provider is not None else pd.DataFrame()
            except Exception as exc:
                fired, trigger_reason, live_spot = False, "provider data unavailable: " + type(exc).__name__, None
                quotes = pd.DataFrame()
            setup["trigger"] = trigger_reason
            if not time(9, 15) < entry_at.time() < time(15, 15) or entry_at.weekday() >= 5:
                fired = False
                setup["trigger"] = "outside entry session"
            risk_legs, depth = [], []
            for leg in legs:
                spec = master.resolve(ticker, leg["expiry"], entry_at.date())
                px = leg.get("meta", {}).get("validated_premium")
                if px is None or not math.isfinite(float(px)) or px <= 0:
                    setup["reasons"].append("validated EOD premium unavailable")
                live = False
                iv_premium = px
                if provider is not None:
                    q = quotes
                    if not q.empty:
                        q = q.loc[(q.index <= entry_at) & (q.index >= entry_at - pd.Timedelta(seconds=policy.max_quote_age_seconds)) &
                                  (q.expiry == leg["expiry"]) & (q.strike == leg["strike"]) & (q.option_type == leg["option_type"])]
                    if q.empty:
                        setup["reasons"].append("fresh exact-contract quote unavailable")
                    else:
                        row = q.iloc[-1]
                        if not all(math.isfinite(float(row[k])) for k in ("bid", "ask", "bid_size", "ask_size")) or row.bid <= 0 or row.ask < row.bid or min(row.bid_size, row.ask_size) < 0:
                            setup["reasons"].append("invalid quote/depth")
                        else:
                            live = True
                            iv_premium = float((row.ask + row.bid) / 2)
                            px = float(row.ask if leg["direction"] == "long" else row.bid)
                            spread = (row.ask - row.bid) / row.ask * 100
                            setup["spread_pct"] = max(setup["spread_pct"], spread)
                            if spread > policy.max_spread_pct:
                                setup["reasons"].append("spread exceeds limit")
                            if spec:
                                depth.append(int(min(row.ask_size, row.bid_size) // spec.lot_size))
                if spec is None or spec.settlement != "cash":
                    setup["reasons"].append("listed contract specification/lot size unavailable")
                spot = live_spot if live and live_spot else item["spot"]
                iv_at = entry_at if live else pd.Timestamp(str(asof) + " 15:30", tz="Asia/Kolkata")
                iv_days = expiry_days(leg["expiry"], iv_at)
                iv = implied_volatility(iv_premium or 0, spot, leg["strike"], iv_days / 365, settings.RISK_FREE_RATE, leg["option_type"])
                context = contract_iv_context(iv, history, ticker, leg["option_type"], iv_days,
                                              leg["strike"] / spot, iv_at, policy.min_iv_sessions)
                if iv is not None:
                    report["iv_observations"].append(dict(timestamp=iv_at.isoformat(), underlying=ticker,
                        option_type=leg["option_type"], expiry=str(leg["expiry"]), strike=leg["strike"],
                        iv=iv, dte=iv_days, moneyness=leg["strike"] / spot))
                vol_label = label in {"Long Straddle (Low IV)", "Long Strangle (Low IV)", "Short Strangle (High IV)", "Iron Condor"}
                if vol_label:
                    pct = context["percentile"]
                    if pct is None:
                        setup["reasons"].append(context["reason"])
                    elif (label.startswith("Long") and pct > policy.low_iv_percentile) or (not label.startswith("Long") and pct < policy.high_iv_percentile):
                        setup["reasons"].append("contract IV percentile threshold not met")
                output_leg = {"contract": contract_key(ticker, leg), "direction": leg["direction"], "iv": context,
                              "lot_size": spec.lot_size if spec else None, "units": 0, "premium": px,
                              "quote_basis": "observed_bid_ask" if live else "EOD_estimate"}
                setup["legs"].append(output_leg)
                if spec and px and math.isfinite(px) and px > 0:
                    side = "buy" if leg["direction"] == "long" else "sell"
                    fee = calculate_transaction_cost(px, spec.lot_size, 1, side, entry_at.date())
                    # Reserve estimated round-trip costs, not only opening brokerage.
                    exit_fee = calculate_transaction_cost(px, spec.lot_size, 1, "sell" if side == "buy" else "buy", entry_at.date())
                    risk_legs.append(SimpleNamespace(underlying=ticker, expiry=leg["expiry"], lot_size=spec.lot_size,
                        strike=leg["strike"], option_type=leg["option_type"], direction=leg["direction"], entry_premium=px,
                        entry_cost=fee + exit_fee))
            try:
                if len(risk_legs) != len(legs):
                    # Missing specifications prevent sizing; they do not imply missing strategy legs.
                    raise ValueError("sizing unavailable until all contract specifications and premiums are present")
                validate_structure(risk_legs, legs[0].get("structure_type", "single"))
                loss, debit = expiry_risk(risk_legs)
                if legs[0].get("structure_type") == "short_strangle" or (len(legs) == 1 and legs[0]["direction"] == "short"):
                    raise ValueError("uncovered shorts require broker margin; disabled in daily report")
                setup["max_loss_per_lot"] = loss
                setup["capital_per_lot"] = max(loss, debit)
            except ValueError as exc:
                setup["reasons"].append(str(exc))
            setup["depth_lots"] = min(depth) if len(depth) == len(legs) else 0
            setup["trigger_observed"] = fired
            candidates.append(setup)
    candidates.sort(key=lambda s: (bool(s["reasons"]), not s["trigger_observed"], s["spread_pct"], s["strategy"], s["underlying"]))
    selected = set()
    for setup in candidates:
        ticker = setup["underlying"]
        if setup["reasons"]:
            report["no_trade_reasons"].append(f"{ticker} {setup['strategy']}: " + "; ".join(sorted(set(setup["reasons"]))))
            availability_only = set(setup["reasons"]) <= {
                "listed contract specification/lot size unavailable", "fresh exact-contract quote unavailable",
                "sizing unavailable until all contract specifications and premiums are present"}
            if availability_only and ticker not in selected:
                selected.add(ticker)
                report["awaiting_triggers"].append(setup)
            else:
                report["alternatives"].append(setup)
            continue
        if ticker in selected:
            setup["reasons"].append("alternative; one primary per underlying")
            report["alternatives"].append(setup)
            continue
        allowed, reason = rm.can_open_trade()
        lots = rm.structure_size(setup["max_loss_per_lot"], setup["capital_per_lot"]) if allowed else 0
        if any(exposure[l["contract"]] != 0 for l in setup["legs"]):
            lots, reason = 0, "existing duplicate contract exposure; additional allocation blocked"
        if provider is not None:
            lots = min(lots, setup["depth_lots"])
        if lots <= 0:
            setup["reasons"].append(reason if not allowed or "duplicate" in reason else "whole structure unaffordable or insufficient depth")
            report["no_trade_reasons"].append(f"{ticker} {setup['strategy']}: {setup['reasons'][-1]}")
            report["alternatives"].append(setup)
            continue
        selected.add(ticker)
        setup["estimated_lots"] = lots
        confirmed = provider is not None and setup["trigger_observed"] and report["portfolio_verified"]
        setup["lots"] = lots if confirmed else 0
        for leg in setup["legs"]:
            leg["units"] = setup["lots"] * leg["lot_size"]
            leg["estimated_units"] = lots * leg["lot_size"]
            exposure[leg["contract"]] += (1 if leg["direction"] == "long" else -1) * lots * leg["lot_size"]
        rm.register_open(f"suggested-{ticker}", lots * setup["capital_per_lot"], lots * setup["max_loss_per_lot"])
        setup["whole_trade_budget"] = lots * setup["max_loss_per_lot"]
        if not report["portfolio_verified"]:
            setup["trigger"] += "; portfolio snapshot unverified"
        report["confirmed_entries" if confirmed else "awaiting_triggers"].append(setup)
    report["aggregate_contract_units_including_estimates"] = dict(exposure)
    report["no_trade_reasons"] = list(dict.fromkeys(report["no_trade_reasons"]))
    report["report_generated"] = True
    report["actionable"] = bool(report["confirmed_entries"])
    if not candidates:
        report["no_trade_reasons"].append("No validated EOD setups; missing data is not evidence of a no-signal market")
    return report


def render_daily_report(report):
    lines = [f"# NSE options daily report — {report['asof']}", "", f"Intended entry: {report['entry_at']}",
             f"Provider: {report['provider']}. {report['sizing_basis']}.",
             f"Portfolio verified and fresh: {report['portfolio_verified']}. Fees: {report['fee_basis']}.",
             "India VIX is a market regime feature, not contract IV percentile.", "", report["ranking"]]
    for key, title in (("confirmed_entries", "Confirmed entries"), ("awaiting_triggers", "Setups awaiting triggers"), ("alternatives", "Alternatives / ineligible setups")):
        lines += ["", f"## {title}", ""]
        if not report[key]:
            lines.append("None.")
        for s in report[key]:
            lines.append(f"- {s['underlying']} — {s['strategy']}: {s['trigger']}; entry DTE {s['entry_dte']:.3f}; lots {s['lots']}, estimated lots {s['estimated_lots']}.")
            lines.append("  - Confirmation: " + s["confirmation_rule"])
            if "whole_trade_budget" in s:
                lines.append(f"  - Whole-trade risk budget including estimated fees: INR {s['whole_trade_budget']:.2f}.")
            for l in s["legs"]:
                iv = f"{l['iv']['iv'] * 100:.2f}%" if l['iv']['iv'] is not None else "unavailable"
                pct = f"{l['iv']['percentile']:.1f}" if l['iv']['percentile'] is not None else "unavailable"
                lines.append(f"  - {l['direction']} {l['contract']}; lot size {l['lot_size'] or 'unavailable'}; units {l['units']}; IV {iv}; percentile {pct} ({l['iv']['samples']} prior sessions). {l['iv']['reason']}")
            if s["reasons"]:
                lines.append("  - " + "; ".join(dict.fromkeys(s["reasons"])))
    lines += ["", "## No-trade reasons", ""] + [f"- {r}" for r in report["no_trade_reasons"]]
    lines += ["", "No trade is required each day. Quotes are observations, not guaranteed fills. No accuracy improvement has been established."]
    return "\n".join(lines)


def save_daily_report(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (output / "report.md").write_text(render_daily_report(report), encoding="utf-8")
    pd.DataFrame(report["iv_observations"]).drop_duplicates().to_csv(output / "iv_observations.csv", index=False)
    return output / "report.md"
