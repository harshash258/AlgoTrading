"""
Weekly review email for replaying the strategy book over the completed week.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape
from pathlib import Path

import pandas as pd

import config
import algo_trading.core.backtester as _backtester_module
from algo_trading.core.backtester import Backtester
from algo_trading.data.ingestion import ChainLookup, get_combined_dataset, load_bhavcopy
from algo_trading.notifications.telegram import TICKER_NAMES, _all_strategies
from algo_trading.core.regime import regime_for_trade, summarize_regime_performance
from algo_trading.reporting.metrics import compute_metrics, trades_to_dataframe


@dataclass
class WeeklyReviewResult:
    week_start: date
    week_end: date
    html_path: Path
    csv_path: Path
    summary_path: Path
    total_trades: int
    net_pnl: float
    email_sent: bool = False
    email_status: str = ""


def completed_week_window(as_of: date | None = None) -> tuple[date, date]:
    """
    Return the Monday-Friday window to review.

    On Saturday/Sunday this reviews the week that just ended. On weekdays it
    reviews the current week to date, which is useful for manual runs.
    """
    as_of = as_of or date.today()
    week_start = as_of - timedelta(days=as_of.weekday())
    week_end = min(week_start + timedelta(days=4), as_of)
    if as_of.weekday() >= 5:
        week_end = week_start + timedelta(days=4)
    return week_start, week_end


def build_weekly_review(
    as_of: date | None = None,
    tickers: list[str] | None = None,
    send_email: bool = True,
) -> WeeklyReviewResult:
    week_start, week_end = completed_week_window(as_of)
    tickers = tickers or config.UNDERLYINGS
    data_start = week_start - timedelta(days=420)

    data = get_combined_dataset(
        start=data_start,
        end=week_end,
        force_refresh=False,
    )
    data = {k: v for k, v in data.items() if k in tickers}
    if not data:
        raise RuntimeError(f"No market data available for {tickers}")

    trade_frames: list[pd.DataFrame] = []
    metric_rows: list[dict] = []
    previous_chain_lookup = _backtester_module._chain_lookup
    previous_chain_lookups = dict(_backtester_module._chain_lookups)
    _backtester_module._chain_lookup = None
    _backtester_module._chain_lookups = _weekly_chain_lookups(week_start, week_end)

    try:
        for strategy_label, strategy in _all_strategies():
            bt = Backtester(
                strategy=strategy,
                data={k: v.copy() for k, v in data.items()},
                start=week_start,
                end=week_end,
                starting_capital=config.STARTING_CAPITAL,
            )
            trades = bt.run()
            equity_curve = bt.get_equity_curve()
            metrics = compute_metrics(trades, equity_curve, config.STARTING_CAPITAL)

            metric_rows.append({
                "Strategy": strategy_label,
                "Trades": metrics["total_trades"],
                "Win %": metrics["win_rate_pct"],
                "Net P&L": metrics["net_pnl"],
                "Return %": metrics["total_return_pct"],
                "Avg Trade %": metrics["avg_trade_pct"],
                "Profit Factor": metrics["profit_factor"],
                "Max DD %": metrics["max_drawdown_pct"],
                "Sharpe": metrics["sharpe_ratio"],
            })

            if trades:
                df = trades_to_dataframe(trades)
                df.insert(0, "Strategy", strategy_label)
                df["underlying_name"] = df["underlying"].map(TICKER_NAMES).fillna(df["underlying"])
                df["Regime"] = [regime_for_trade(trade, data) for trade in trades]
                trade_frames.append(df)
    finally:
        _backtester_module._chain_lookup = previous_chain_lookup
        _backtester_module._chain_lookups = previous_chain_lookups

    trades_df = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    metrics_df = pd.DataFrame(metric_rows)
    regime_df = summarize_regime_performance(
        trades_df,
        min_trades=getattr(config, "MIN_REGIME_TRADES_FOR_RECOMMENDATION", 5),
    )
    output_dir = Path(config.WEEKLY_REVIEW_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = f"{week_start.isoformat()}_to_{week_end.isoformat()}"
    csv_path = output_dir / f"weekly_trades_{stamp}.csv"
    html_path = output_dir / f"weekly_review_{stamp}.html"
    summary_path = output_dir / f"weekly_metrics_{stamp}.csv"

    trades_df.to_csv(csv_path, index=False)
    metrics_df.to_csv(summary_path, index=False)
    html = render_weekly_html(week_start, week_end, metrics_df, trades_df, regime_df)
    html_path.write_text(html, encoding="utf-8")

    result = WeeklyReviewResult(
        week_start=week_start,
        week_end=week_end,
        html_path=html_path,
        csv_path=csv_path,
        summary_path=summary_path,
        total_trades=int(len(trades_df)),
        net_pnl=float(trades_df["net_pnl"].sum()) if not trades_df.empty else 0.0,
    )

    if send_email:
        sent, status = send_weekly_email(result, html)
        result.email_sent = sent
        result.email_status = status
    else:
        result.email_status = "Email skipped by flag."

    return result


def render_weekly_html(
    week_start: date,
    week_end: date,
    metrics_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    regime_df: pd.DataFrame | None = None,
) -> str:
    top = metrics_df.sort_values(
        ["Profit Factor", "Net P&L"],
        ascending=[False, False],
        na_position="last",
    ).head(8)

    total_pnl = float(trades_df["net_pnl"].sum()) if not trades_df.empty else 0.0
    total_trades = len(trades_df)
    wins = int((trades_df["net_pnl"] > 0).sum()) if not trades_df.empty else 0
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    fallback_vix = int(trades_df["vix_source"].isin(["fallback", "stale"]).sum()) if "vix_source" in trades_df else 0

    def table(df: pd.DataFrame) -> str:
        if df.empty:
            return "<p>No trades closed in this review window.</p>"
        return df.to_html(index=False, escape=True, classes="data")

    recent_cols = [
        c for c in [
            "Strategy", "signal_date", "entry_date", "exit_date", "underlying_name",
            "structure_type", "leg_label", "option_type", "direction", "strike",
            "expiry", "net_pnl", "pnl_pct", "held_days", "exit_reason",
            "entry_iv", "pricing_source", "vix_source", "Regime",
        ] if c in trades_df.columns
    ]
    recent = trades_df[recent_cols].sort_values(
        ["exit_date", "Strategy"],
        ascending=[False, True],
    ).head(50) if not trades_df.empty else trades_df
    regime_df = regime_df if regime_df is not None else pd.DataFrame()

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Weekly Algo Review {week_start} to {week_end}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #182026; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .muted {{ color: #64717d; }}
    .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 18px 0; }}
    .card {{ border: 1px solid #d7dde3; border-radius: 6px; padding: 12px 14px; min-width: 150px; }}
    .label {{ color: #64717d; font-size: 12px; text-transform: uppercase; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table.data {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }}
    table.data th, table.data td {{ border: 1px solid #d7dde3; padding: 7px 8px; text-align: right; }}
    table.data th {{ background: #f3f6f8; color: #1f2a33; }}
    table.data td:first-child, table.data th:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>Weekly Algo Review</h1>
  <div class="muted">{escape(str(week_start))} to {escape(str(week_end))}</div>
  <div class="cards">
    <div class="card"><div class="label">Closed Trades</div><div class="value">{total_trades}</div></div>
    <div class="card"><div class="label">Net P&L</div><div class="value">₹{total_pnl:,.0f}</div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value">{win_rate:.1f}%</div></div>
    <div class="card"><div class="label">Fallback/Stale VIX Legs</div><div class="value">{fallback_vix}</div></div>
  </div>
  <h2>Strategy Scoreboard</h2>
  {table(top)}
  <h2>Regime Performance</h2>
  {table(regime_df)}
  <h2>Recent Closed Trades</h2>
  {table(recent)}
  <p class="muted">Use the CSV attachments for deeper threshold and parameter tuning.</p>
</body>
</html>
"""


def send_weekly_email(result: WeeklyReviewResult, html_body: str) -> tuple[bool, str]:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("WEEKLY_EMAIL_FROM", username)
    recipients = _email_recipients()

    if not host or not sender or not recipients:
        return False, "SMTP_HOST, WEEKLY_EMAIL_FROM/SMTP_USERNAME, or WEEKLY_EMAIL_TO not configured."

    msg = EmailMessage()
    msg["Subject"] = f"Weekly Algo Review: {result.week_start} to {result.week_end}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Weekly Algo Review {result.week_start} to {result.week_end}\n"
        f"Closed trades: {result.total_trades}\n"
        f"Net P&L: INR {result.net_pnl:,.0f}\n\n"
        f"Open the attached HTML report for the strategy scoreboard."
    )
    msg.add_alternative(html_body, subtype="html")

    for path in (result.html_path, result.csv_path, result.summary_path):
        data = path.read_bytes()
        maintype, subtype = ("text", "html") if path.suffix == ".html" else ("text", "csv")
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    use_tls = os.environ.get("SMTP_TLS", str(config.SMTP_TLS)).lower() not in {"0", "false", "no"}
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
        return True, f"Email sent to {', '.join(recipients)}."
    except Exception as exc:
        return False, f"Email failed: {exc}"


def _email_recipients() -> list[str]:
    raw = os.environ.get("WEEKLY_EMAIL_TO", "")
    recipients = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    if recipients:
        return recipients
    return list(getattr(config, "WEEKLY_REVIEW_RECIPIENTS", []))


def _equity_curve_from_trades(trades, week_start: date, week_end: date) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()

    rows = [{"date": week_start, "capital": config.STARTING_CAPITAL}]
    capital = config.STARTING_CAPITAL
    for trade in sorted(trades, key=lambda item: item.exit_date or week_end):
        capital += trade.net_pnl
        rows.append({
            "date": trade.exit_date or week_end,
            "capital": capital,
        })
    return pd.DataFrame(rows).set_index("date")


class _NoChainLookup:
    def has_data_for(self, _date) -> bool:
        return False


def _weekly_chain_lookup(week_start: date, week_end: date):
    lookups = _weekly_chain_lookups(week_start, week_end)
    return next(iter(lookups.values()), _NoChainLookup())


def _weekly_chain_lookups(week_start: date, week_end: date) -> dict[str, ChainLookup | None]:
    folder = getattr(config, "BHAVCOPY_FOLDER", None)
    if not folder:
        return {}
    bhavcopy_dir = Path.cwd() / folder
    if not bhavcopy_dir.is_dir():
        return {}

    paths = []
    day = week_start
    while day <= week_end:
        if day.weekday() < 5:
            name = f"fo{day.strftime('%d%b%Y').upper()}bhav.csv.zip"
            path = bhavcopy_dir / name
            if path.exists():
                paths.append(path)
        day += timedelta(days=1)

    if not paths:
        return {}

    lookups: dict[str, ChainLookup | None] = {}
    symbols = set(getattr(config, "BHAVCOPY_SYMBOLS", {}).values()) or {config.BHAVCOPY_SYMBOL}
    for symbol in symbols:
        frames = []
        for path in paths:
            try:
                frames.append(load_bhavcopy(str(path), symbol=symbol, verbose=False))
            except Exception:
                continue
        lookups[symbol] = ChainLookup(pd.concat(frames, ignore_index=True)) if frames else None
    return lookups


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()
