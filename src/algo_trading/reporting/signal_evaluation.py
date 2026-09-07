"""Chronological strategy selection and untouched final-session holdout.

Uses the existing quote-driven intraday execution engine, not EOD proxy fills.
Report coverage is derived from a separate report journal, never trade counts.
"""
import json
from pathlib import Path
import pandas as pd
from algo_trading.core.intraday import IntradayBacktester
from algo_trading.core.volatility import expiry_days, tenor_bucket


def summary(rows):
    values = pd.Series([r["net_pnl"] for r in rows], dtype=float)
    equity = pd.concat([pd.Series([0.0]), values.cumsum()], ignore_index=True)
    return dict(trade_count=len(rows), win_rate=float((values > 0).mean()) if len(rows) else None,
                net_expectancy=float(values.mean()) if len(rows) else None,
                net_pnl=float(values.sum()), max_drawdown_rupees=float((equity.cummax() - equity).max()))


def breakdown(rows):
    result = {}
    for dimension in ("strategy", "underlying", "expiry_distance", "market_condition", "expiry_day_policy"):
        result[dimension] = {value: summary([r for r in rows if r[dimension] == value])
                             for value in sorted({r[dimension] for r in rows})}
    return result


def coverage(sessions, reports):
    expected = {str(d) for d in sessions}
    generated = {r["asof"] for r in reports if r.get("report_generated") and r["asof"] in expected}
    actionable = {r["asof"] for r in reports if r.get("actionable") and r["asof"] in generated}
    return dict(expected_sessions=len(expected), reports_generated=len(generated), actionable_sessions=len(actionable),
                report_coverage=len(generated)/len(expected) if expected else None,
                actionable_frequency=len(actionable)/len(expected) if expected else None)


def chronological_evaluation(bars, quotes, ticker, master, train_sessions=60, test_sessions=20,
                             holdout_sessions=20, starting_capital=500000, reports=()):
    if min(train_sessions, test_sessions, holdout_sessions) < 1:
        raise ValueError("Positive chronological window sizes required")
    sessions = sorted(set(bars.index.date))
    if len(sessions) < train_sessions + test_sessions + holdout_sessions:
        raise ValueError("Insufficient sessions for training, validation and untouched holdout")
    development = sessions[:-holdout_sessions]
    policies = [(strategy, expiry_day) for strategy in ("orb", "vwap") for expiry_day in (False, True)]
    # Conditions use prior-session returns only, not the test day's closing regime.
    closes = bars.Close.groupby(bars.index.date).last()
    prior_returns = closes.pct_change().shift(1)

    def run(days, policy):
        strategy, expiry_day = policy
        b = bars.loc[pd.Index(bars.index.date).isin(days)]
        q = quotes.loc[(quotes.index >= b.index.min() - pd.Timedelta(seconds=30)) & (quotes.index <= b.index.max())]
        engine = IntradayBacktester(b, q, ticker, master, strategy, starting_capital,
                                   max_spread_pct=5, allow_expiry_day=expiry_day)
        trades = engine.run()
        rows = []
        for trade in trades:
            prior = prior_returns.get(trade.entry_date.date(), float("nan"))
            condition = "unknown" if pd.isna(prior) else "prior_up" if prior > .005 else "prior_down" if prior < -.005 else "prior_flat"
            rows.append(dict(entry_at=str(trade.entry_date), exit_at=str(trade.exit_date), strategy=strategy,
                underlying=ticker, net_pnl=trade.net_pnl, expiry_distance=tenor_bucket(expiry_days(trade.expiry, trade.entry_date)),
                market_condition=condition, expiry_day_policy="enabled" if expiry_day else "disabled"))
        equity = pd.Series([starting_capital] + [r["capital"] for r in engine.equity_curve], dtype=float)
        return rows, float((equity.cummax() - equity).max())

    def select(days):
        training = []
        for policy in policies:
            rows, drawdown = run(days, policy)
            metrics = dict(summary(rows), observed_equity_drawdown_rupees=drawdown)
            training.append((policy, metrics))
        eligible = [(p, m) for p, m in training if m["trade_count"] > 0]
        winner = max(eligible, key=lambda x: x[1]["net_expectancy"])[0] if eligible else None
        return winner, [{"strategy": p[0], "allow_expiry_day": p[1], **m} for p, m in training]

    folds, oos = [], []
    for split in range(train_sessions, len(development), test_sessions):
        train, test = development[:split], development[split:split+test_sessions]
        winner, scores = select(train)
        rows, drawdown = run(test, winner) if winner else ([], 0.0)
        oos.extend(rows)
        folds.append(dict(train_end=str(train[-1]), test_start=str(test[0]), test_end=str(test[-1]),
                          selected=winner, training=scores, metrics=dict(summary(rows), observed_equity_drawdown_rupees=drawdown)))
    winner, scores = select(development)
    holdout, holdout_drawdown = run(sessions[-holdout_sessions:], winner) if winner else ([], 0.0)
    return dict(folds=folds, validation=summary(oos), validation_breakdown=breakdown(oos),
                holdout={"start": str(sessions[-holdout_sessions]), "selected": winner,
                         "training": scores, "metrics": dict(summary(holdout), observed_equity_drawdown_rupees=holdout_drawdown), "breakdown": breakdown(holdout)},
                coverage=coverage(sessions, reports), trades=oos + holdout,
                limitations=["Fixed ORB/VWAP candidate selection; does not validate all EOD watchlist strategies",
                    "Breakdown drawdown uses closed-trade cumulative P&L; folds also include observed intraday equity drawdown",
                    "Each fold starts with the stated capital; no positions cross session boundaries",
                    "Fees use the configured fee schedule or labeled current-rate proxy",
                    "No-trade training windows select no strategy; holdout must not be reused for tuning"])
