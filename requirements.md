# Options Trading System — Requirements Document

## 1. Project Overview

A Python-based algorithmic options trading system for Indian markets (NSE).
The system backtests multi-day options strategies (buying and selling) across
all NSE-listed underlyings, generates trade signals, and produces a detailed
performance report. Trades are executed manually by the user on Groww based
on system-generated signals.

---

## 2. Scope

- **Market**: NSE (India) — Nifty 50 index, Bank Nifty index, Nifty Midcap, and
  liquid individual stocks with active options chains
- **Instruments**: Options (Calls and Puts) — both buying and selling
- **Strategy type**: Directional and non-directional, multi-day (positional)
- **Execution**: Manual by user; system provides entry/exit signals only
- **Backtest period**: 5 years of historical data

---

## 3. Data Requirements

### 3.1 Spot/Underlying Data
- Source: Yahoo Finance (`yfinance` Python library) as primary free source
- Fallback: NSEPy, NSE India unofficial API, or Jugaad Trader
- Data needed: Daily OHLCV for all target underlyings
- Lookback: 5 years minimum

### 3.2 Options Chain Data
- **This is the hardest part with free data sources.**
- Yahoo Finance does NOT provide historical Indian options chain data
- Approach: Synthetic options pricing using Black-Scholes model
  - Use historical spot price + implied volatility estimates to reconstruct
    approximate historical option premiums
  - IV source: VIX India (^INDIAVIX on Yahoo Finance) as a proxy for market IV
- Alternative if synthetic is insufficient: NSE website scraping for recent
  data, or user to procure historical options data from a vendor (True Data,
  Quantsapp) at a later stage
- Data points needed per option: Strike, Expiry, Type (CE/PE), Premium (Open,
  High, Low, Close), OI, Volume, IV

### 3.3 Expiry Calendar
- Monthly expiry (last Thursday of month) for index options
- Weekly expiry (every Thursday) for Nifty and Bank Nifty
- System must correctly map trade dates to nearest/next expiry

---

## 4. System Modules

### 4.1 Data Ingestion Module
- Download and cache historical spot data via yfinance
- Compute or fetch historical IV
- Generate synthetic options chain data using Black-Scholes
- Store data locally in CSV or SQLite for reuse (avoid re-downloading)
- Support incremental updates (fetch only new data since last run)

### 4.2 Strategy Engine
- Pluggable strategy architecture — each strategy is a standalone Python class
- Strategy interface must define:
  - `generate_signals(data)` → returns list of trade signals
  - `signal` schema: underlying, direction (long/short), option type (CE/PE),
    strike selection method (ATM / OTM by delta or % away), expiry selection,
    entry date, conditions
- Initial strategies to implement:
  - **Strategy 1**: Trend-following — buy ATM CE/PE based on moving average
    crossover on underlying
  - **Strategy 2**: Mean reversion — sell OTM strangles when IV is elevated
  - More strategies added iteratively

### 4.3 Options Pricing Module
- Black-Scholes pricing for European options (standard for NSE index options)
- Inputs: Spot price, Strike, Time to expiry (in years), Risk-free rate
  (91-day T-bill rate, ~6.5% currently), IV
- Outputs: Option premium (theoretical), Delta, Gamma, Theta, Vega
- Greeks used for strike selection and position sizing

### 4.4 Backtesting Engine
- Simulate trades day by day over the 5-year window
- For each trade:
  - Entry: Record entry date, entry premium, underlying price at entry
  - Exit: Based on strategy exit rules (target %, stop loss %, expiry, or
    signal reversal)
  - P&L = (Exit premium - Entry premium) × lot size × direction multiplier
- Account for:
  - **Brokerage**: Flat ₹20 per order (Groww pricing) or 0.05% whichever lower
  - **STT**: 0.125% on sell side for options (buying) / both sides for
    shorting
  - **Exchange charges**: NSE transaction charges ~0.053% of premium
  - **GST**: 18% on brokerage + exchange charges
  - **SEBI charges**: ₹10 per crore
  - **Stamp duty**: 0.003% on buy side
- Lot sizes: Use current NSE lot sizes (Nifty = 25, Bank Nifty = 15, etc.)
- Capital: Configurable starting capital (default ₹5,00,000)
- Position sizing: Fixed fractional — risk configurable % of capital per trade
  (default 2%)

### 4.5 Risk Management Rules
- Max open positions at any time: configurable (default 3)
- Per-trade max loss: configurable stop loss % on premium (default 50% of
  premium paid for buying; 100% of premium received for selling)
- Per-trade target: configurable (default 100% for buying, 50% for selling)
- No new positions if drawdown exceeds configurable threshold (default 15%
  from peak capital)
- Expiry rule: Close all positions 1 day before expiry if not already exited

### 4.6 Performance Reporting Module
Compute and display the following metrics after each backtest run:

| Metric | Description |
|---|---|
| Total Trades | Count of completed trades |
| Win Rate % | Winning trades / total trades |
| Total Return % | Final capital vs starting capital |
| Avg Trade % | Mean return per trade |
| Median Trade % | Median return per trade |
| Best Trade % | Single best trade return |
| Worst Trade % | Single worst trade return |
| Avg Win % | Mean return of winning trades |
| Avg Loss % | Mean return of losing trades |
| Profit Factor | Gross profit / gross loss |
| Sharpe Ratio (per trade) | Mean trade return / std dev of trade returns |
| Sortino Ratio (per trade) | Mean trade return / std dev of losing trade returns only (downside deviation) |
| CAGR % | Compound Annual Growth Rate over the backtest period |
| Max Drawdown % | Largest peak-to-trough capital decline |
| Avg Held Days | Average number of calendar days per trade |

- Output: Console summary table + exportable CSV of all trades
- Equity curve: Matplotlib chart of capital over time
- Trade log: CSV with all trade details (entry, exit, P&L, greeks at entry)

### 4.7 HTML Dashboard Report

After every backtest run, auto-generate a self-contained HTML report file in
`reports/` named `report_<strategy>_<date>.html`. No server required — opens
directly in any browser.

**Dashboard sections:**

#### A. Summary Header
- Strategy name, underlying(s), backtest period (from → to)
- Starting capital, ending capital, net P&L in ₹
- All 13 performance metrics displayed as a clean card/tile grid
- Color coding: green for positive metrics, red for negative (e.g., drawdown,
  avg loss)

#### B. Equity Curve
- Interactive line chart of capital value over time
- Drawdown area shaded below the peak equity line
- Hover tooltip showing date, capital value, drawdown % at that point
- Library: Plotly (renders inline in HTML, no external dependency at runtime)

#### C. Monthly Returns Heatmap
- Calendar-style grid: rows = years, columns = months (Jan–Dec)
- Cell color = monthly return % (green positive, red negative, white near zero)
- Cell value shows the return % number
- Helps identify seasonal patterns and bad months at a glance

#### D. Trade Log Table
- Sortable, filterable table of every trade with columns:
  - #, Date Entry, Date Exit, Underlying, Type (CE/PE), Strike, Expiry,
    Direction (Long/Short), Entry Premium ₹, Exit Premium ₹, P&L ₹, P&L %,
    Held Days, Exit Reason (Target / SL / Expiry / Signal)
- Row color: green for winning trades, red for losing trades
- Search box to filter by underlying or date range
- Pagination (50 rows per page)

#### E. Win/Loss Distribution
- Histogram of trade returns (%) — shows distribution of outcomes
- Separate bars for wins (green) and losses (red)
- Vertical lines marking avg win and avg loss

#### F. Trade P&L Over Time (Bar Chart)
- Each bar = one trade, colored green/red by outcome
- X-axis = trade entry date, Y-axis = P&L %
- Running cumulative P&L line overlaid

#### G. Strategy Parameters Panel
- Read-only display of all config values used for this backtest run
  (capital, stop loss %, target %, position sizing, risk-free rate, slippage,
  brokerage model, etc.)
- Ensures every report is fully reproducible — parameters are embedded

#### H. Daily Signal Section (if run in signal mode)
- Table of today's actionable signals with full trade details
- Printable / copy-paste friendly format

**Technical requirements for HTML report:**
- Fully self-contained: all CSS, JS (Plotly), and data embedded inline — no
  internet connection needed to open
- Generated using Python `Jinja2` templating engine
- Plotly charts serialized as JSON and embedded in the HTML
- Responsive layout — readable on laptop and tablet
- Dark/light mode toggle (optional, nice to have)
- File size target: under 5 MB per report

---

## 5. Signal Delivery

Since trades are manual, the system will output a **daily signal report**:
- Run the system each day (before market open, 9:00–9:15 AM IST)
- Output: Plain text or CSV listing
  - Action (BUY / SELL / EXIT)
  - Underlying
  - Option type (CE/PE)
  - Strike price
  - Expiry date
  - Suggested entry price range (based on previous day's close ± buffer)
  - Stop loss level
  - Target level
  - Lot size
  - Approx capital required

---

## 6. Technology Stack

| Component | Choice |
|---|---|
| Language | Python 3.10+ |
| Data fetching | `yfinance`, `nsepy` (fallback) |
| Options pricing | `mibian` or custom Black-Scholes implementation |
| Numerical compute | `numpy`, `pandas` |
| Backtesting framework | Custom engine (not Backtrader — options support is weak) |
| Visualization | `matplotlib`, `seaborn`, `plotly` |
| HTML reporting | `Jinja2` (templating), `plotly` (interactive charts) |
| Storage | SQLite via `sqlite3` or flat CSV files |
| Scheduling (future) | `schedule` or `APScheduler` for daily signal runs |
| Environment | `venv`, dependencies in `requirements.txt` |

---

## 7. Project Structure

```
algo-trading/
├── data/
│   ├── raw/              # Downloaded spot data
│   ├── options/          # Synthetic or scraped options data
│   └── cache/            # SQLite DB or pickled DataFrames
├── src/
│   ├── data_ingestion.py
│   ├── options_pricing.py
│   ├── strategy_engine.py
│   ├── strategies/
│   │   ├── base_strategy.py
│   │   ├── trend_following.py
│   │   └── mean_reversion.py
│   ├── backtester.py
│   ├── risk_manager.py
│   ├── reporter.py
│   └── html_report.py    # HTML dashboard generator (Jinja2 + Plotly)
├── templates/            # Jinja2 HTML templates for dashboard
│   └── report_template.html
├── signals/              # Daily signal output files (CSV + HTML)
├── reports/              # Backtest report CSVs, charts, and HTML dashboards
├── config.py             # All configurable parameters
├── main.py               # Entry point — run backtest or generate signals
├── requirements.txt
└── README.md
```

---

## 8. Constraints and Assumptions

- Free data sources mean options pricing is synthetic (Black-Scholes), not
  actual market fills — backtest results are indicative, not exact
- Groww does not have a trading API; all signals are for manual execution only
- Slippage assumption: 1–2% of premium on entry and exit (configurable)
- Risk-free rate: Fixed at 6.5% annually (can be updated in config)
- No dividend adjustment for individual stock options in initial version
- System does not account for liquidity constraints (bid-ask spread) beyond
  the slippage assumption

---

## 9. Out of Scope (for now)

- Automated order placement
- Real-time intraday signals
- Options portfolio hedging / delta-neutral management
- Broker API integration (Groww has no public API)
- Futures trading
- Screener for finding new strategy candidates

---

## 10. Success Criteria

The system is considered ready when:
1. Backtest runs cleanly over 5 years for at least 2 strategies
2. All 13 performance metrics are computed and displayed correctly
3. Daily signal output is readable and actionable without ambiguity
4. Brokerage and tax costs are correctly deducted from P&L
5. Code is modular enough to add a new strategy in under 30 minutes
