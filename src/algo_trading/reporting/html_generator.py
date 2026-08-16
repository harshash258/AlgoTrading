"""
html_report.py — Generates self-contained HTML dashboard reports.

Uses Jinja2 for templating and Plotly for interactive charts.
All JS/CSS/data is embedded inline — no internet required to open.
"""

import os
import json
import logging
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.utils
from jinja2 import Environment, FileSystemLoader, select_autoescape

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from algo_trading.core.backtester import Trade
from algo_trading.reporting.metrics import compute_metrics, trades_to_dataframe

logger = logging.getLogger(__name__)


def generate_html_report(
    trades: list[Trade],
    equity_curve: pd.DataFrame,
    strategy_name: str,
    strategy_params: dict,
    underlyings: list[str],
    backtest_start: date,
    backtest_end: date,
    signals: list[dict] | None = None,
    output_dir: str = config.REPORTS_DIR,
) -> str:
    """
    Generate a self-contained HTML report and save to output_dir.

    Returns the path to the saved HTML file.
    """
    os.makedirs(output_dir, exist_ok=True)

    metrics  = compute_metrics(trades, equity_curve, config.STARTING_CAPITAL)
    trades_df = trades_to_dataframe(trades)

    # Build all chart JSON
    equity_chart    = _equity_curve_chart(equity_curve)
    heatmap_chart   = _monthly_heatmap(equity_curve, config.STARTING_CAPITAL)
    histogram_chart = _pnl_histogram(trades_df)
    bar_chart       = _pnl_bar_chart(trades_df)

    # Render template
    template_dir = os.path.join(os.path.dirname(__file__), "..", config.TEMPLATES_DIR)
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report_template.html")

    html = template.render(
        strategy_name    = strategy_name,
        underlyings      = ", ".join(underlyings),
        backtest_start   = backtest_start,
        backtest_end     = backtest_end,
        metrics          = metrics,
        strategy_params  = strategy_params,
        trades_json      = trades_df.to_json(orient="records", date_format="iso"),
        equity_chart     = equity_chart,
        heatmap_chart    = heatmap_chart,
        histogram_chart  = histogram_chart,
        bar_chart        = bar_chart,
        signals          = signals or [],
        generated_at     = datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows_per_page    = config.REPORT_ROWS_PER_PAGE,
    )

    filename = f"report_{strategy_name}_{date.today().isoformat()}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"HTML report saved: {filepath}")
    return filepath


# ─────────────────────────────────────────────────────────────────
# Chart builders — all return Plotly JSON strings
# ─────────────────────────────────────────────────────────────────

def _to_json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def _equity_curve_chart(equity_curve: pd.DataFrame) -> str:
    """Interactive equity curve with drawdown shading."""
    if equity_curve.empty:
        return "{}"

    dates   = [str(d) for d in equity_curve.index]
    capital = equity_curve["capital"].tolist()
    peak    = equity_curve["capital"].cummax().tolist()
    dd_pct  = [
        (c - p) / p * 100 if p > 0 else 0
        for c, p in zip(capital, peak)
    ]

    fig = go.Figure()

    # Drawdown fill
    fig.add_trace(go.Scatter(
        x=dates, y=peak,
        fill=None, mode="lines",
        line=dict(width=0),
        showlegend=False,
        name="Peak",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=capital,
        fill="tonexty",
        fillcolor="rgba(220,50,50,0.15)",
        mode="lines",
        line=dict(color="#e74c3c", width=0.5),
        showlegend=False,
        name="Drawdown",
    ))

    # Equity line
    fig.add_trace(go.Scatter(
        x=dates, y=capital,
        mode="lines",
        line=dict(color="#2ecc71", width=2),
        name="Portfolio Value",
        hovertemplate="<b>%{x}</b><br>₹%{y:,.0f}<extra></extra>",
    ))

    fig.update_layout(
        title="Equity Curve",
        xaxis_title="Date",
        yaxis_title="Capital (₹)",
        template="plotly_dark",
        height=400,
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return _to_json(fig)


def _monthly_heatmap(equity_curve: pd.DataFrame, starting_capital: float) -> str:
    """Calendar heatmap of monthly returns."""
    if equity_curve.empty:
        return "{}"

    ec = equity_curve.copy()
    ec.index = pd.to_datetime(ec.index)
    ec["year"]  = ec.index.year
    ec["month"] = ec.index.month

    # Monthly return = last capital of month / last capital of prev month - 1
    monthly = ec.groupby(["year", "month"])["capital"].last().reset_index()
    monthly["prev"] = monthly["capital"].shift(1)
    monthly.iloc[0, monthly.columns.get_loc("prev")] = starting_capital
    monthly["return_pct"] = (monthly["capital"] - monthly["prev"]) / monthly["prev"] * 100

    years  = sorted(monthly["year"].unique())
    months = list(range(1, 13))
    month_labels = ["Jan","Feb","Mar","Apr","May","Jun",
                    "Jul","Aug","Sep","Oct","Nov","Dec"]

    z      = []
    text   = []
    for year in years:
        row      = []
        text_row = []
        for m in months:
            val = monthly.loc[
                (monthly["year"] == year) & (monthly["month"] == m), "return_pct"
            ]
            if val.empty:
                row.append(None)
                text_row.append("")
            else:
                v = round(float(val.iloc[0]), 2)
                row.append(v)
                text_row.append(f"{v:+.2f}%")
        z.append(row)
        text.append(text_row)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=month_labels,
        y=[str(y) for y in years],
        text=text,
        texttemplate="%{text}",
        colorscale=[
            [0.0, "#c0392b"],
            [0.5, "#ffffff"],
            [1.0, "#27ae60"],
        ],
        zmid=0,
        hovertemplate="<b>%{y} %{x}</b><br>Return: %{text}<extra></extra>",
    ))
    fig.update_layout(
        title="Monthly Returns Heatmap",
        template="plotly_dark",
        height=max(200, len(years) * 50 + 80),
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return _to_json(fig)


def _pnl_histogram(trades_df: pd.DataFrame) -> str:
    """Win/Loss distribution histogram."""
    if trades_df.empty:
        return "{}"

    wins   = trades_df.loc[trades_df["pnl_pct"] > 0, "pnl_pct"]
    losses = trades_df.loc[trades_df["pnl_pct"] <= 0, "pnl_pct"]

    fig = go.Figure()
    if not wins.empty:
        fig.add_trace(go.Histogram(
            x=wins, name="Wins",
            marker_color="#27ae60", opacity=0.75,
            hovertemplate="Return: %{x:.2f}%<br>Count: %{y}<extra></extra>",
        ))
    if not losses.empty:
        fig.add_trace(go.Histogram(
            x=losses, name="Losses",
            marker_color="#e74c3c", opacity=0.75,
            hovertemplate="Return: %{x:.2f}%<br>Count: %{y}<extra></extra>",
        ))

    # Avg win / avg loss vertical lines
    if not wins.empty:
        fig.add_vline(x=wins.mean(), line_dash="dash", line_color="#2ecc71",
                      annotation_text=f"Avg Win {wins.mean():.1f}%")
    if not losses.empty:
        fig.add_vline(x=losses.mean(), line_dash="dash", line_color="#e74c3c",
                      annotation_text=f"Avg Loss {losses.mean():.1f}%")

    fig.update_layout(
        title="Trade Return Distribution",
        xaxis_title="Return %",
        yaxis_title="Count",
        barmode="overlay",
        template="plotly_dark",
        height=350,
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return _to_json(fig)


def _pnl_bar_chart(trades_df: pd.DataFrame) -> str:
    """Per-trade P&L bar chart with cumulative line."""
    if trades_df.empty:
        return "{}"

    colors  = ["#27ae60" if v > 0 else "#e74c3c" for v in trades_df["pnl_pct"]]
    cum_pnl = trades_df["net_pnl"].cumsum().tolist()
    labels  = [str(d)[:10] for d in trades_df["entry_date"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=trades_df["pnl_pct"].tolist(),
        marker_color=colors,
        name="Trade P&L %",
        hovertemplate="<b>%{x}</b><br>P&L: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=cum_pnl,
        mode="lines",
        line=dict(color="#f39c12", width=2),
        name="Cumulative P&L ₹",
        yaxis="y2",
        hovertemplate="Cum P&L: ₹%{y:,.0f}<extra></extra>",
    ))

    fig.update_layout(
        title="Trade P&L & Cumulative Returns",
        xaxis_title="Entry Date",
        yaxis_title="P&L %",
        yaxis2=dict(
            title="Cumulative P&L ₹",
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        template="plotly_dark",
        height=380,
        margin=dict(l=60, r=80, t=50, b=60),
    )
    return _to_json(fig)
