# Algo Trading — NSE Options System

Python-based backtesting, signal generation, and parameter optimization for NSE options (India).
Supports both buying and selling strategies, multi-day positional trades.
Generates interactive HTML reports with full trade logs and charts.

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

| Key        | Strategy                         | Type                           |
|------------|----------------------------------|--------------------------------|
| `trend`    | MA Crossover (fast/slow)         | Long options, directional      |
| `rsi`      | RSI Reversal                     | Long options, mean-reversion   |
| `combined` | MA Crossover + RSI (both active) | Long options, dual-signal      |
| `mean_rev` | Short Strangle on High IV        | Short options, non-directional |

---

## Parameter Optimization

The system includes a two-level optimization framework to find the best parameter combination
for the combined strategy (MA Crossover + RSI) and associated risk settings.

### Level 1 — Grid Search (`src/optimizer.py`)

Searches all valid combinations of:

| Parameter       | Values tested              |
|-----------------|----------------------------|
| `fast_ma`       | 5, 10, 15, 20, 25          |
| `slow_ma`       | 30, 40, 50, 60, 75         |
| `rsi_oversold`  | 25, 30, 35, 40             |
| `rsi_overbought`| 60, 65, 70, 75             |
| `stop_loss_pct` | 40, 50, 60, 70             |
| `target_pct`    | 75, 100, 150, 200          |

Invalid combos are automatically skipped (`fast_ma >= slow_ma`, `oversold >= overbought`).
Total valid combinations: **6,400** (full grid) or **144** (quick grid).

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

| Setting       | Value  |
|---------------|--------|
| Train window  | 3 years|
| Test window   | 1 year |
| Step          | 1 year (rolling) |

**Example folds for 2015-2026 data:**

| Fold | Train Period       | Test Period |
|------|--------------------|-------------|
| 1    | 2015-01-01 → 2017-12-31 | 2018 |
| 2    | 2016-01-01 → 2018-12-31 | 2019 |
| 3    | 2017-01-01 → 2019-12-31 | 2020 |
| 4    | 2018-01-01 → 2020-12-31 | 2021 |
| 5    | 2019-01-01 → 2021-12-31 | 2022 |
| 6    | 2020-01-01 → 2022-12-31 | 2023 |
| 7    | 2021-01-01 → 2023-12-31 | 2024 |
| 8    | 2022-01-01 → 2024-12-31 | 2025 |

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

The system can use real historical NSE F&O option chain data instead of synthetic
Black-Scholes pricing. This significantly improves backtest accuracy.

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
Set `BHAVCOPY_FOLDER = "data/bhavcopy"` in `config.py` to activate real pricing.

---

## Configuration

All parameters are in `config.py`. These are the key ones:

| Parameter             | Default    | Description                                  |
|-----------------------|------------|----------------------------------------------|
| `BACKTEST_START`      | 2015-01-01 | Backtest start date                          |
| `BACKTEST_END`        | today      | Backtest end date                            |
| `STARTING_CAPITAL`    | ₹5,00,000  | Initial capital                              |
| `RISK_PER_TRADE_PCT`  | 2%         | Capital at risk per trade                    |
| `MAX_OPEN_POSITIONS`  | 5          | Max concurrent positions                     |
| `MAX_LOTS_PER_TRADE`  | 10         | Hard cap on lots per single trade            |
| `MAX_DRAWDOWN_HALT_PCT`| 15%       | Halt new trades if drawdown exceeds this     |
| `BUY_STOP_LOSS_PCT`   | 50%        | Stop loss for long options (% of premium)    |
| `BUY_TARGET_PCT`      | 100%       | Profit target for long options               |
| `SLIPPAGE_PCT`        | 1.5%       | Slippage assumption on entry and exit        |
| `TREND_FAST_MA`       | 20         | Fast MA period for trend strategy            |
| `TREND_SLOW_MA`       | 50         | Slow MA period for trend strategy            |
| `RSI_OVERSOLD`        | 35         | RSI level to buy CE (oversold reversal)      |
| `RSI_OVERBOUGHT`      | 65         | RSI level to buy PE (overbought reversal)    |
| `BHAVCOPY_FOLDER`     | data/bhavcopy | Folder containing NSE bhavcopy ZIPs       |
| `BHAVCOPY_SYMBOL`     | NIFTY      | NSE symbol to filter from bhavcopy files     |
| `UNDERLYINGS`         | ^NSEI, ^NSEBANK, NIFTYFIN.NS | Tickers to trade          |

---

## Project Structure

```
algo-trading/
├── main.py                       # CLI entry point
├── config.py                     # All parameters
├── requirements.txt              # Dependencies
├── src/
│   ├── data_ingestion.py         # yfinance data fetching + bhavcopy loader
│   ├── options_pricing.py        # Black-Scholes + Greeks + expiry helpers
│   ├── backtester.py             # Core backtest engine
│   ├── risk_manager.py           # Position sizing + SL/target rules
│   ├── reporter.py               # Metrics computation + console output
│   ├── html_report.py            # HTML dashboard generator
│   ├── optimizer.py              # Level 1: Grid search optimization
│   ├── walk_forward.py           # Level 2: Walk-forward validation
│   ├── bhavcopy_downloader.py    # NSE bhavcopy file downloader
│   └── strategies/
│       ├── base_strategy.py      # Abstract base class + Signal dataclass
│       ├── trend_following.py    # MA Crossover (with ADX, confirm, direction filters)
│       ├── rsi_strategy.py       # RSI Reversal (Wilder smoothing)
│       ├── combined_strategy.py  # Meta-strategy: merges signals from N strategies
│       └── mean_reversion.py     # Short Strangle on High IV
├── templates/
│   └── report_template.html      # Jinja2 HTML template for reports
├── data/
│   ├── bhavcopy/                 # NSE F&O bhavcopy ZIP files
│   ├── cache/                    # Intermediate data cache
│   ├── options/                  # Options data
│   └── raw/                      # Downloaded OHLCV CSVs
├── reports/                      # HTML reports, trade CSVs, optimization results
└── signals/                      # Daily signal output files
```

---

## Performance Metrics

Every backtest and optimization run reports these metrics:

| Metric             | Description                                              |
|--------------------|----------------------------------------------------------|
| Total Trades       | Number of closed trades                                  |
| Win Rate %         | % of trades with positive P&L                           |
| Total Return %     | Overall return on starting capital                       |
| CAGR %             | Compound Annual Growth Rate                              |
| Profit Factor      | Gross wins / gross losses (>1.0 = profitable)            |
| Sharpe per Trade   | Mean trade return / std dev of trade returns             |
| Sortino per Trade  | Mean return / std dev of losing trades (downside only)   |
| Max Drawdown %     | Largest peak-to-trough equity decline                    |
| Avg Held Days      | Average trade duration in calendar days                  |

---

## Adding a New Strategy

1. Create `src/strategies/my_strategy.py`
2. Inherit from `BaseStrategy`
3. Implement the `name` property and `generate_signals(data, vix, current_date)` method
4. Return a list of `Signal` objects (entry or exit)
5. Register it in the `get_strategy()` factory in `main.py`

To include it in optimization, update the `_run_single()` function in `src/optimizer.py`.

---

## Baseline Performance (pre-optimization)

| Metric         | Value      |
|----------------|------------|
| Strategy       | Combined MA(20/50) + RSI(35/65) |
| Total Trades   | 64         |
| Win Rate       | 29.69%     |
| Total Return   | -11.01%    |
| Profit Factor  | 0.794      |
| Max Drawdown   | 15.83%     |

Run `python main.py optimize` to search for parameter sets that beat this baseline.

---

## Important Disclaimer

- Options pricing uses Black-Scholes with India VIX as IV proxy when bhavcopy data is unavailable
- Backtest results are indicative only — not a guarantee of future performance
- This is not financial advice. All trades are your own decision.
- Groww has no trading API — all signals must be executed manually
