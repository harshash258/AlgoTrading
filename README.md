# Algo Trading — NSE Options System

Python-based backtesting, signal generation, and parameter optimization for NSE options (India).
Supports both buying and selling strategies, multi-day positional trades.
Generates interactive HTML reports with full trade logs and charts.
Daily signals delivered automatically via Telegram using GitHub Actions.

---

## Quick Start

### 1. Install dependencies

```bash
cd "d:\Projects\Algo Trading"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run your first backtest (Combined strategy on Nifty 50)

```bash
python main.py backtest --strategy combined --ticker ^NSEI
```

This will:
- Download historical Nifty 50 + India VIX data (cached after first run)
- Run MA crossover + RSI combined strategy, simulate options trades
- Print a metrics summary in the terminal
- Save an HTML report to `reports/` — open in browser

### 3. Generate today's signals

```bash
python main.py signals --strategy combined --ticker ^NSEI
```

### 4. Refresh data cache

```bash
python main.py fetch
```

---

## Available Strategies

| Key          | Strategy                         | Type                           |
|--------------|----------------------------------|--------------------------------|
| `trend`      | MA Crossover (fast/slow)         | Long options, directional      |
| `trend_raw`  | MA Crossover (no filters)        | Long options, directional      |
| `rsi`        | RSI Reversal (Wilder smoothing)  | Long options, mean-reversion   |
| `combined`   | MA Crossover + RSI (both active) | Long options, dual-signal      |
| `mean_rev`   | Short Strangle on High IV        | Short options, non-directional |

The `combined` strategy is the default and recommended choice. It runs both MA crossover and
RSI signals simultaneously — each strategy maintains independent state and the risk manager
enforces the global position cap.

---

## Parameter Optimization

The system includes a two-level optimization framework to find the best parameter combination
for the combined strategy (MA Crossover + RSI) and associated risk settings.

### Level 1 — Grid Search (`src/optimizer.py`)

Searches all valid combinations of:

| Parameter        | Values tested              |
|------------------|----------------------------|
| `fast_ma`        | 5, 10, 15, 20, 25          |
| `slow_ma`        | 30, 40, 50, 60, 75         |
| `rsi_oversold`   | 25, 30, 35, 40             |
| `rsi_overbought` | 60, 65, 70, 75             |
| `stop_loss_pct`  | 40, 50, 60, 70             |
| `target_pct`     | 75, 100, 150, 200          |

Invalid combos are automatically skipped (`fast_ma >= slow_ma`, `oversold >= overbought`).
Total valid combinations: **6,400** (full grid) or **144** (quick grid).

**Key optimization insight:** `stop_loss_pct` and `target_pct` don't affect signal generation —
only exit timing. Signal backtests run once per (fast_ma, slow_ma, rsi_oversold, rsi_overbought)
combo, then trades are replayed with different SL/target settings in O(trades) time. This reduces
actual backtests from 6,400 → 400 (16x speedup).

**Ranking**: Primary metric is Profit Factor (most robust, least sensitive to outliers).
Tiebreaker is Sharpe per trade. Results with fewer than 10 trades are excluded.

**What it does:**
- Loads the NSE bhavcopy option chain **once** at startup (30-60s), reuses it for all runs
- Patches config values in-memory per run — `config.py` is never modified during the search
- Creates fresh strategy instances per run to avoid state bleed
- Prints a live progress bar: `Testing 47/6400 | Best so far: PF=2.14 (fast=10, slow=40, os=30, ob=70)`
- Saves ranked results to `reports/optimization_results_YYYY-MM-DD.csv`
- Auto-updates `config.py` with the top-ranked parameter set and prints a diff

**Run it:**

```bash
# Full grid search (~6,400 combinations)
python main.py optimize

# Quick grid (~144 combinations, faster for testing)
python main.py optimize --quick

# Full grid, then run Level 2 walk-forward on top 5 results
python main.py optimize --top 5

# Custom minimum trades threshold
python main.py optimize --min-trades 20

# Run on a specific underlying only
python main.py optimize --ticker ^NSEI
```

**Output CSV columns:**

```
rank, fast_ma, slow_ma, rsi_oversold, rsi_overbought, stop_loss_pct, target_pct,
total_trades, win_rate_pct, total_return_pct, cagr_pct, profit_factor,
sharpe_per_trade, sortino_per_trade, max_drawdown_pct, avg_held_days
```

### Level 2 — Walk-Forward Validation (`src/walk_forward.py`)

Validates the top N parameter sets from Level 1 against **unseen out-of-sample periods**
to check whether the results are genuinely robust or just in-sample overfitting.

**Rolling window logic (defaults):**

| Setting       | Value   |
|---------------|---------|
| Train window  | 3 years |
| Test window   | 1 year  |
| Step          | 1 year (rolling) |

**Example folds for 2015-2026 data:**

| Fold | Train Period            | Test Period |
|------|-------------------------|-------------|
| 1    | 2015-01-01 → 2017-12-31 | 2018        |
| 2    | 2016-01-01 → 2018-12-31 | 2019        |
| 3    | 2017-01-01 → 2019-12-31 | 2020        |
| 4    | 2018-01-01 → 2020-12-31 | 2021        |
| 5    | 2019-01-01 → 2021-12-31 | 2022        |
| 6    | 2020-01-01 → 2022-12-31 | 2023        |
| 7    | 2021-01-01 → 2023-12-31 | 2024        |
| 8    | 2022-01-01 → 2024-12-31 | 2025        |

**Per parameter set, it reports:**
- Per-fold table: `fold | train_period | test_period | train_pf | test_pf | return% | win_rate | dd%`
- Mean and std of out-of-sample profit factor across all folds
- Consistency score: % of folds where profit factor > 1.0 (profitable)
- Overfitting flag: raised when in-sample PF exceeds out-of-sample PF by more than 50%

**Final recommendation printed:**
```
RECOMMENDED PARAMS: fast_ma=X, slow_ma=Y, rsi_oversold=Z, rsi_overbought=W,
stop_loss_pct=A, target_pct=B
Consistent across N/8 folds (X% profitable), avg OOS profit_factor=Z
```

**Output saved to:** `reports/walk_forward_results_YYYY-MM-DD.csv`

---

## NSE Bhavcopy (Real Options Chain Data)

The system uses real historical NSE F&O option chain data instead of synthetic Black-Scholes
pricing by default (`BHAVCOPY_FOLDER = "data/bhavcopy"` in config.py). This significantly
improves backtest accuracy.

### Download bhavcopy files

```bash
# Download full history (2015 to yesterday)
python main.py bhavcopy

# Download last 30 days only (quick test)
python main.py bhavcopy --days 30

# Download a specific date range
python main.py bhavcopy --start 2023-01-01 --end 2024-12-31

# Re-download existing files
python main.py bhavcopy --overwrite

# Verify downloaded ZIP files
python main.py bhavcopy --verify
```

Files are saved to `data/bhavcopy/` as `fo<DD><MON><YYYY>bhav.csv.zip`.
Set `BHAVCOPY_FOLDER = None` in `config.py` to fall back to synthetic Black-Scholes pricing.

The `ChainLookup` class in `data_ingestion.py` provides fast (date, expiry, strike, opt_type)
→ premium lookup over the loaded chain. It handles multi-year NSE schema variations automatically.

---

## Daily Signals via Telegram

The system sends automated daily trading signals to a Telegram chat using GitHub Actions.

### How it works

The GitHub Actions workflow (`.github/workflows/daily_signals.yml`) runs every weekday at
4:15 PM IST (10:45 UTC) — 45 minutes after NSE market close, ensuring yfinance has today's
candle. It can also be triggered manually from the GitHub UI.

The signal message includes:
- Action (BUY CE / BUY PE) per underlying
- Strike, expiry, spot price, India VIX at time of signal
- Trigger label (MA crossover / RSI reversal)
- Stop-loss %, target %, risk amount, lot size
- Conflict detection: if both CE and PE signals fire on the same underlying, they are
  highlighted with source strategy labels so you can make an informed decision

### Setup

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather) and get your bot token
2. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
3. Add both as GitHub repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### Run manually

```bash
export TELEGRAM_BOT_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
python src/telegram_notify.py
```

---

## Configuration

All parameters are in `config.py`. These are the key ones:

| Parameter              | Default          | Description                                  |
|------------------------|------------------|----------------------------------------------|
| `BACKTEST_START`       | 2015-01-01       | Backtest start date                          |
| `BACKTEST_END`         | today            | Backtest end date                            |
| `STARTING_CAPITAL`     | ₹5,00,000        | Initial capital                              |
| `RISK_PER_TRADE_PCT`   | 2%               | Capital at risk per trade                    |
| `MAX_OPEN_POSITIONS`   | 5                | Max concurrent positions                     |
| `MAX_LOTS_PER_TRADE`   | 10               | Hard cap on lots per single trade            |
| `MAX_DRAWDOWN_HALT_PCT`| 15%              | Halt new trades if drawdown exceeds this     |
| `BUY_STOP_LOSS_PCT`    | 40%              | Stop loss for long options (% of premium)    |
| `BUY_TARGET_PCT`       | 200%             | Profit target for long options               |
| `SELL_STOP_LOSS_PCT`   | 100%             | Stop loss for short options                  |
| `SELL_TARGET_PCT`      | 50%              | Profit target for short options              |
| `SLIPPAGE_PCT`         | 1.5%             | Slippage assumption on entry and exit        |
| `RISK_FREE_RATE`       | 6.5%             | Annualised risk-free rate (91-day T-bill)    |
| `DAYS_BEFORE_EXPIRY_EXIT` | 1            | Close positions N days before expiry        |
| `TREND_FAST_MA`        | 25               | Fast MA period for trend strategy            |
| `TREND_SLOW_MA`        | 75               | Slow MA period for trend strategy            |
| `RSI_PERIOD`           | 14               | RSI calculation period                       |
| `RSI_OVERSOLD`         | 25               | RSI level to buy CE (oversold reversal)      |
| `RSI_OVERBOUGHT`       | 65               | RSI level to buy PE (overbought reversal)    |
| `RSI_CONFIRM_BARS`     | 1                | Bars RSI must hold past threshold            |
| `RSI_TIME_STOP_DAYS`   | 10               | Exit RSI trades if no profit within N days   |
| `BHAVCOPY_FOLDER`      | data/bhavcopy    | Folder containing NSE bhavcopy ZIPs          |
| `BHAVCOPY_SYMBOL`      | NIFTY            | NSE symbol to filter from bhavcopy files     |
| `UNDERLYINGS`          | ^NSEI, ^NSEBANK, NIFTY_FIN_SERVICE.NS | Tickers to trade   |

After running `python main.py optimize`, the best-found parameters are automatically written
back to `config.py` and a diff is printed showing what changed.

---

## Project Structure

```
algo-trading/
├── main.py                        # CLI entry point
├── config.py                      # All parameters
├── requirements.txt               # Dependencies
├── src/
│   ├── data_ingestion.py          # yfinance data fetching + bhavcopy loader + ChainLookup
│   ├── options_pricing.py         # Black-Scholes pricing + Greeks + expiry helpers
│   ├── backtester.py              # Core event-driven backtest engine
│   ├── risk_manager.py            # Position sizing + SL/target rules + drawdown gating
│   ├── reporter.py                # 20+ performance metrics + console summary
│   ├── html_report.py             # HTML dashboard generator (Jinja2 + Plotly)
│   ├── optimizer.py               # Level 1: Grid search (16x speedup via signal replay)
│   ├── walk_forward.py            # Level 2: Walk-forward validation with overfitting detection
│   ├── bhavcopy_downloader.py     # NSE bhavcopy file downloader
│   ├── telegram_notify.py         # Daily signal delivery via Telegram Bot API
│   └── strategies/
│       ├── base_strategy.py       # Abstract base class + Signal dataclass
│       ├── trend_following.py     # MA Crossover (ADX, confirm, direction, time-stop filters)
│       ├── rsi_strategy.py        # RSI Reversal (Wilder smoothing, confirmation)
│       ├── combined_strategy.py   # Meta-strategy: merges signals from N strategies
│       └── mean_reversion.py      # Short Strangle on High IV percentile
├── templates/
│   └── report_template.html       # Jinja2 HTML template for reports
├── .github/
│   └── workflows/
│       └── daily_signals.yml      # GitHub Actions: daily Telegram signal delivery
├── data/
│   ├── bhavcopy/                  # NSE F&O bhavcopy ZIP files (2,300+ files)
│   ├── cache/                     # Intermediate data cache
│   ├── options/                   # Options data
│   └── raw/                       # Downloaded OHLCV CSVs (^NSEI, ^NSEBANK, ^INDIAVIX)
├── reports/                       # HTML reports, trade CSVs, optimization results
└── signals/                       # Daily signal output files
```

---

## Performance Metrics

Every backtest and optimization run reports these metrics:

| Metric              | Description                                              |
|---------------------|----------------------------------------------------------|
| Total Trades        | Number of closed trades                                  |
| Win Rate %          | % of trades with positive P&L                           |
| Total Return %      | Overall return on starting capital                       |
| CAGR %              | Compound Annual Growth Rate                              |
| Avg Trade %         | Mean return per trade                                    |
| Median Trade %      | Median return per trade                                  |
| Best / Worst Trade  | Single best and worst trade returns                      |
| Avg Win / Avg Loss  | Mean return of winning and losing trades                 |
| Profit Factor       | Gross wins / gross losses (>1.0 = profitable)            |
| Sharpe per Trade    | Mean trade return / std dev of trade returns             |
| Sortino per Trade   | Mean return / std dev of losing trades (downside only)   |
| Max Drawdown %      | Largest peak-to-trough equity decline                    |
| Avg Held Days       | Average trade duration in calendar days                  |
| Avg Entry Delta     | Mean absolute delta of options at entry                  |
| Avg Theta %/day     | Mean daily theta decay as % of premium paid              |

---

## HTML Dashboard Report

After every backtest, a self-contained HTML report is saved to `reports/`. Sections:

- **Summary header** — all metrics as a card grid, color-coded
- **Equity curve** — interactive Plotly chart with drawdown shading
- **Monthly returns heatmap** — calendar grid of monthly P&L %
- **Trade log table** — sortable/filterable, paginated, green/red rows
- **Win/Loss distribution** — histogram with avg win/loss lines
- **Trade P&L bar chart** — per-trade bars with cumulative P&L overlay
- **Strategy parameters panel** — all config values used, embedded for reproducibility

All JS/CSS/Plotly data is embedded inline — no internet required to open.

---

## Adding a New Strategy

1. Create `src/strategies/my_strategy.py`
2. Inherit from `BaseStrategy`
3. Implement the `name` property and `generate_signals(data, vix, current_date)` method
4. Return a list of `Signal` objects (entry or exit)
5. Register it in the `get_strategy()` factory in `main.py`

To include it in optimization, update the `_run_signal_backtest()` function in `src/optimizer.py`.

---

## Brokerage & Tax Model

All P&L calculations deduct real transaction costs using Groww's flat-fee model:

| Cost              | Rate                                       |
|-------------------|--------------------------------------------|
| Brokerage         | ₹20 flat per order (or 0.05%, whichever lower) |
| STT               | 0.125% on sell side (options)              |
| Exchange charges  | 0.053% of premium                          |
| GST               | 18% on brokerage + exchange charges        |
| SEBI charges      | ₹10 per crore turnover                     |
| Stamp duty        | 0.003% on buy side                         |

---

## Important Disclaimer

- Options pricing falls back to Black-Scholes with India VIX as IV proxy when bhavcopy data
  is unavailable for a given date
- Backtest results are indicative only — not a guarantee of future performance
- This is not financial advice. All trades are your own decision
- Groww has no trading API — all signals must be executed manually
