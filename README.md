# Algo Trading — NSE Options System

Python-based backtesting, signal generation, strategy exploration, and parameter optimization
for NSE index options (India). Supports directional, volatility, mean-reversion, and
delta-neutral strategies. Generates interactive HTML reports with full trade logs and charts.
Daily signals delivered automatically via Telegram using GitHub Actions.

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

To run unit and regression tests:
```bash
pytest tests/
```

If `python` is not found or `.venv\Scripts\python.exe` points to an inaccessible interpreter,
delete and recreate the virtual environment with the same install commands above.

### 2. Run your first backtest

```bash
python main.py backtest --strategy combined --ticker ^NSEI
```

This downloads Nifty 50 + India VIX data (cached after first run), runs the MA crossover + RSI
combined strategy, simulates options trades, prints a metrics summary, and saves an HTML report
to `reports/`. Open it in any browser.

### 3. Generate today's signals

```bash
python main.py signals --strategy combined --ticker ^NSEI
```

### 4. Find the best strategy combination

```bash
python strategy_explorer.py                        # singles + pairs, all tickers
python strategy_explorer.py --max-combo 3          # up to 3-strategy combos
python strategy_explorer.py --no-bhavcopy          # fast run using Black-Scholes
```

### 5. Refresh data cache

```bash
python main.py fetch
```

### 6. Screen stocks for trading opportunities

```bash
# Screen all 2,404 NSE stocks (~12-15 minutes with 4 workers)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt

# Speed up with more workers (6-8 recommended for CI/CD)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt --max-workers 8

# Or use conservative settings (2 workers)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt --max-workers 2
```

Results saved to `reports/screen_cheap_to_moon_YYYY-MM-DD.csv`

---

## Available Strategies

13 strategies across 4 categories. Mix any combination using `--strategy` or the
strategy explorer.

### Directional (long options, pick direction)

| Key           | Strategy                          | Signal source                                 |
|---------------|-----------------------------------|-----------------------------------------------|
| `trend`       | MA Crossover (basic)              | Fast MA crosses above/below slow MA           |
| `trend_full`  | MA Crossover (all filters on)     | MA cross + ADX + confirmation + 200MA filter  |
| `rsi`         | RSI Reversal                      | RSI crosses oversold/overbought thresholds    |
| `confluence`  | MA + RSI Agreement                | Both MA and RSI must agree on direction       |
| `orb`         | Opening Range Breakout            | Close > Open ± N% (daily breakout proxy)      |

### Volatility / Mean Reversion (non-directional)

| Key           | Strategy                          | Signal source                                 |
|---------------|-----------------------------------|-----------------------------------------------|
| `mean_rev`    | Short Strangle on High IV         | IV percentile > threshold → sell CE + PE      |
| `bb`          | Bollinger Band Reversion          | Price touches band edge, reverts to mean      |
| `iron_condor` | Iron Condor (delta-neutral)       | High IV + choppy (ADX < threshold) → 4-leg   |

### Long Volatility (buy vol cheap, profit from expansion)

| Key           | Strategy                          | Signal source                                 |
|---------------|-----------------------------------|-----------------------------------------------|
| `straddle`    | Long ATM Straddle                 | IV percentile < threshold → buy CE + PE ATM   |
| `strangle`    | Long OTM Strangle                 | IV percentile < threshold → buy OTM CE + PE   |

### Intraday-proxy / Price Action

| Key           | Strategy                          | Signal source                                 |
|---------------|-----------------------------------|-----------------------------------------------|
| `vwap_rev`    | VWAP Reversion                    | Price crosses back inside VWAP σ-band         |
| `vwap_brk`    | VWAP Breakout                     | Price breaks and holds outside VWAP σ-band    |
| `gap_fade`    | Gap Fade                          | Opening gap > N% with no follow-through       |

### Meta-strategies

| Key               | Description                                             |
|-------------------|---------------------------------------------------------|
| `combined`        | Runs MA crossover + RSI simultaneously, merges signals  |
| `inv_<any>`       | Inverts any strategy — flips CE↔PE on every signal      |

**Telegram bot uses:** `combined` (MA Crossover 25/75 + RSI 14, thresholds 25/65).

---

## Strategy Design Philosophy

The strategy book is designed to cover all market regimes:

| Regime       | Suitable strategies                          |
|--------------|----------------------------------------------|
| Trending up  | `trend`, `orb`, `confluence`, `vwap_brk`     |
| Trending down| `trend`, `orb` (PE), `gap_fade`              |
| Choppy/range | `iron_condor`, `mean_rev`, `vwap_rev`, `bb`  |
| Low IV day   | `straddle`, `strangle`                       |
| High IV day  | `mean_rev`, `iron_condor`                    |
| Gap open     | `gap_fade`                                   |

**Vol balance:** `mean_rev` and `iron_condor` sell premium (high IV) while `straddle`/`strangle`
buy premium (low IV) — a natural pair. The combined strategy book is designed so something works
in any regime rather than everything working only in one.

---

## Running Backtests

```bash
# Single strategy
python main.py backtest --strategy orb --ticker ^NSEI

# Combined strategy on multiple tickers
python main.py backtest --strategy combined

# Iron condor on BankNifty
python main.py backtest --strategy iron_condor --ticker ^NSEBANK

# Invert any strategy (flip CE↔PE)
python main.py backtest --strategy inv_trend --ticker ^NSEI

# Force-refresh market data before backtest
python main.py backtest --strategy combined --refresh
```

HTML report saved to `reports/report_<strategy>_<date>.html`. Trade CSV saved alongside it.

---

## Strategy Explorer

`strategy_explorer.py` tests every single strategy, every pair, and (optionally) every
3+ combination by running full backtests and ranking by any metric.

```bash
# All singles + pairs (13 singles + 78 pairs = 91 backtests)
python strategy_explorer.py

# Up to triples (~377 backtests)
python strategy_explorer.py --max-combo 3

# Only test specific strategies
python strategy_explorer.py --include trend rsi straddle iron_condor

# Exclude strategies
python strategy_explorer.py --exclude vwap_rev vwap_brk gap_fade

# Sort by CAGR instead of profit factor
python strategy_explorer.py --sort cagr_pct

# Fast run (Black-Scholes pricing, no bhavcopy load)
python strategy_explorer.py --no-bhavcopy

# Specific ticker and date range
python strategy_explorer.py --ticker ^NSEI --start 2020-01-01 --end 2024-12-31

# Raise minimum trades threshold
python strategy_explorer.py --min-trades 20

# List all available strategy names
python strategy_explorer.py --list
```

**Sort metrics:** `profit_factor` (default), `cagr_pct`, `total_return_pct`,
`sharpe_per_trade`, `sortino_per_trade`, `win_rate_pct`, `max_drawdown_pct`, `total_trades`.

**Output:**
- Full ranked table (all combos, sorted by chosen metric)
- Breakdown tables by combo size (singles / pairs / triples)
- Best single strategy and best overall combination callouts
- Vol-balance note — flags if top-10 combos pair a short-vol + long-vol strategy
- CSV saved to `reports/strategy_explorer_YYYY-MM-DD.csv`

### Sample Result (^NSEI, 2022–2023, singles only, Black-Scholes)

| Rank | Strategy   | Trades | WR%   | Return% | CAGR%   | PF    | Sharpe | MaxDD% |
|------|------------|--------|-------|---------|---------|-------|--------|--------|
| 1    | trend      | 4      | 25.0% | +1.4%   | +0.7%   | 1.813 | 0.210  | 1.72%  |
| 2    | orb        | 122    | 40.2% | +49.1%  | +22.3%  | 1.522 | 0.155  | 10.28% |
| 3    | rsi        | 13     | 53.9% | +1.9%   | +1.0%   | 1.240 | 0.063  | 5.10%  |
| 4    | vwap_brk   | 38     | 26.3% | +2.2%   | +1.1%   | 1.079 | 0.011  | 11.09% |
| 5    | straddle   | 54     | 35.2% | −2.0%   | −1.0%   | 0.945 | −0.008 | 10.08% |
| 6–10 | (others)  | —      | —     | negative| —       | <0.6  | <0     | 12–16% |

*Note: run over the full 2015–2026 period for statistically meaningful sample sizes.*

---

## Parameter Optimization

Two-level framework to find the best MA × RSI parameter combination.

### Level 1 — Grid Search

```bash
python main.py optimize              # full grid (~6,400 combos)
python main.py optimize --quick      # reduced grid (~144 combos)
python main.py optimize --top 5      # grid search then walk-forward on top 5
python main.py optimize --min-trades 20
python main.py optimize --ticker ^NSEI
```

**What gets searched:**

| Parameter        | Full grid values           | Quick grid values  |
|------------------|----------------------------|--------------------|
| `fast_ma`        | 5, 10, 15, 20, 25          | 10, 20, 25         |
| `slow_ma`        | 30, 40, 50, 60, 75         | 40, 50, 75         |
| `rsi_oversold`   | 25, 30, 35, 40             | 30, 35             |
| `rsi_overbought` | 60, 65, 70, 75             | 65, 70             |
| `stop_loss_pct`  | 40, 50, 60, 70             | 50, 60             |
| `target_pct`     | 75, 100, 150, 200          | 100, 150           |

**Speed trick:** `stop_loss_pct` and `target_pct` don't affect signal generation — only exit
timing. Signals run once per (fast_ma × slow_ma × rsi) combo, then trades are replayed with
different SL/target in O(trades) time. Reduces actual backtests from 6,400 → 400 (16× speedup).

**Ranking:** Primary = Profit Factor. Tiebreaker = Sharpe per trade.

After the run, best parameters are auto-written back to `config.py` with a diff printed.
Results saved to `reports/optimization_results_YYYY-MM-DD.csv`.

### Level 2 — Walk-Forward Validation

Validates top-N param sets against unseen out-of-sample periods to catch overfitting.

```bash
python main.py optimize --top 5      # run Level 1 then Level 2 on top 5
```

**Rolling window (defaults):** 3-year train → 1-year test → step 1 year.

**Example folds (2015–2026):**

| Fold | Train period            | Test period |
|------|-------------------------|-------------|
| 1    | 2015-01-01 → 2017-12-31 | 2018        |
| 2    | 2016-01-01 → 2018-12-31 | 2019        |
| 3    | 2017-01-01 → 2019-12-31 | 2020        |
| 4    | 2018-01-01 → 2020-12-31 | 2021        |
| 5    | 2019-01-01 → 2021-12-31 | 2022        |
| 6    | 2020-01-01 → 2022-12-31 | 2023        |
| 7    | 2021-01-01 → 2023-12-31 | 2024        |
| 8    | 2022-01-01 → 2024-12-31 | 2025        |

**Per param set reports:**
- Per-fold: `fold | train_pf | test_pf | return% | win_rate | dd% | trades`
- Mean and std of out-of-sample profit factor across all folds
- Consistency score: % of folds where PF > 1.0
- Overfitting flag: raised when in-sample PF > out-of-sample PF by more than 50%

Output: `reports/walk_forward_results_YYYY-MM-DD.csv`

---

## NSE Stock Screener (Optimized Architecture)

**NEW (August 2026):** The screener now uses a **two-workflow architecture** for 140-420× speedup:

1. **Monthly Heavy Lifting:** `scripts/update_fundamentals.py` caches fundamental metrics to `data/fundamentals.csv`
2. **Biweekly Fast Screening:** `main.py screen` reads cache + bulk fetches prices (~10-30 seconds)

### How It Works

```
Workflow 1: Monthly (1st of month, 00:00 UTC)
├─ Fetch fundamentals from Screener.in (cached for 25 days)
├─ Fallback to yfinance for missing metrics
└─ Save to data/fundamentals.csv → commit to git

Workflow 2: Biweekly (1st & 15th, 12:30 UTC)
├─ Load data/fundamentals.csv (cached)
├─ Bulk fetch current prices in single yf.download() call
├─ Compute dynamic P/E = price / eps (real-time)
├─ Compute dynamic P/B = price / book_value (real-time)
├─ Apply strategy rules (filter + rank)
└─ Save results to reports/screen_*.csv
```

**Performance Comparison:**

| Metric | Old | New | Improvement |
|--------|-----|-----|------------|
| Screener.in calls | 2,404/run | 0/biweekly | ∞ (moved to monthly) |
| Time per screening | 70 min | 10-30 sec | **140-420× faster** |
| Rate-limiting delays | 70 min | 0 sec | Eliminated |
| Cache approach | None | 25-day fundamentals | Full data reuse |

### Setup: Generate Initial Fundamentals Cache

**First time only: Generate the fundamentals cache (2,404 NSE stocks)**

```bash
# Build full CSV for all 2,404 tickers (~30-60 minutes)
# This fetches real market data from yfinance
python build_full_csv.py

# Once complete, fundamentals are cached in data/fundamentals.csv
# Subsequent screening runs use this cache (no re-fetching needed)
```

**Alternative (if you already have Screener.in data):**

```bash
# Update using Screener.in + yfinance fallback (~80 minutes first time, ~10 min after)
python scripts/update_fundamentals.py

# Or force refresh (skip cache check):
python scripts/update_fundamentals.py --force

# Test with first 50 tickers:
python scripts/update_fundamentals.py --limit 50
```

Creates `data/fundamentals.csv` with all metrics needed for screening.

### Run Screening

```bash
# Single strategy, all 2,404 NSE stocks (~15-30 seconds)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt

# Multiple strategies (batched against same cached data):
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt
python main.py screen --strategy multibagger --universe nse_tickers_template.txt

# Single tickers (instant):
python main.py screen --strategy multibagger --ticker TECHM.NS --ticker SUNPHARMA.NS

# Results saved to:
# reports/screen_cheap_to_moon_YYYY-MM-DD.csv
# reports/screen_multibagger_YYYY-MM-DD.csv
```

### CLI Reference

**Update Fundamentals:**
```bash
python scripts/update_fundamentals.py                       # Normal: skip fresh
python scripts/update_fundamentals.py --force               # Update all (takes ~80 min)
python scripts/update_fundamentals.py --universe custom.txt # Custom ticker file
python scripts/update_fundamentals.py --limit 100           # Test: first 100 only
python scripts/update_fundamentals.py --force --limit 50    # Combine flags
```

**Run Screening:**
```bash
python main.py screen --strategy cheap_to_moon \
  --universe nse_tickers_template.txt

python main.py screen --strategy multibagger \
  --universe nse_tickers_template.txt

# --max-workers flag kept for compatibility (not used in new architecture)
```

# Bulk screen from universe file (all 2,404 NSE stocks)
python main.py screen --strategy multibagger --universe nse_tickers_template.txt

# List all available strategies
python main.py screen --help
```

### Available Screening Strategies

Only 2 focused strategies are available, each designed for a specific market opportunity:

#### 🚀 Cheap to Moon
**Target:** Ultra-cheap penny stocks with positive momentum

| Criterion                  | Value           |
|----------------------------|-----------------|
| P/B Ratio                  | < 1.0           |
| P/E Ratio                  | > 0.5           |
| Stock Price                | < ₹100          |
| Market Cap                 | > ₹20 Cr        |
| ROCE                       | > 5%            |
| Up from 52w low            | > 10%           |

**Use case:** Hunt for severely undervalued micro-caps bouncing back with momentum.

#### � Multibagger
**Target:** High-growth mid-cap stocks with strong fundamentals and promoter backing

| Criterion                  | Value           |
|----------------------------|-----------------|
| Market Cap                 | ₹100–2500 Cr    |
| Annual Sales               | > ₹100 Cr       |
| Sales Growth (3Y CAGR)     | > 15%           |
| Profit Growth (3Y CAGR)    | > 15%           |
| ROCE                       | > 15%           |
| ROE                        | > 15%           |
| Debt/Equity Ratio          | < 0.5           |
| PEG Ratio                  | < 1.5           |
| Promoter Holding           | > 40%           |
| Pledged %                  | < 5%            |
| Up from 52w low            | > 10%           |

**Use case:** Identify quality mid-cap growth stories with excellent fundamentals before
they breakout.

### Output

Results are printed to console and saved to CSV in `reports/screen_<strategy>_YYYY-MM-DD.csv`.

CSV includes: strategy, ticker, price, market cap, P/E, P/B, ROCE, ROE, D/E ratio, 52w movement.

### Creating Your Universe File

Create a text file (e.g., `nse_tickers.txt`) with one ticker per line:

```
RELIANCE.NS
TCS.NS
INFY.NS
WIPRO.NS
HDFCBANK.NS
```

Lines starting with `#` are ignored. Use the included `nse_tickers_template.txt` (all 2,404 NSE
stocks) as a starting point — download via `fetch_nse_symbols.py` or use as-is.

### Data Sources

- **Primary:** Screener.in (comprehensive Indian stock fundamentals)
- **Fallback:** yfinance (price, market cap, 52-week data)

Metrics extracted per stock: P/E, P/B, ROCE, ROE, D/E ratio, promoter holding, pledged %,
3-year sales & profit growth, PEG ratio.

### Performance

- ~1.5 seconds per stock (including Screener.in scrape + yfinance lookup)
- 100 stocks: ~2.5 minutes
- All 2,404 NSE stocks: ~60 minutes
- 0.5s throttle between requests (prevents rate limiting)


---

## NSE Bhavcopy (Real Options Chain Data)



By default the system uses real historical NSE F&O option chain data from bhavcopy files
(`BHAVCOPY_FOLDER = "data/bhavcopy"` in config.py) instead of synthetic Black-Scholes pricing.
This significantly improves backtest accuracy for NIFTY options specifically.

```bash
# Download full history (2015 to yesterday, ~2,300 files)
python main.py bhavcopy

# Download last 30 days only (quick test)
python main.py bhavcopy --days 30

# Specific date range
python main.py bhavcopy --start 2023-01-01 --end 2024-12-31

# Re-download existing files
python main.py bhavcopy --overwrite

# Verify downloaded ZIP files
python main.py bhavcopy --verify
```

Files are saved to `data/bhavcopy/` as `fo<DD><MON><YYYY>bhav.csv.zip`.
Set `BHAVCOPY_FOLDER = None` in config.py to use synthetic Black-Scholes (faster, less accurate).

The `ChainLookup` class provides fast (date, expiry, strike, option_type) → premium lookup.
On any date without bhavcopy data, the system silently falls back to Black-Scholes.

---

## Daily Signals via Telegram

GitHub Actions sends signals every weekday at 4:15 PM IST (45 min after NSE close).

**What the bot sends:**
- BUY CE / BUY PE per underlying
- Long straddle/strangle volatility trades as one paired setup where CE + PE are both required
- Strike, expiry, spot, India VIX, trigger label
- Stop-loss %, target %, risk amount in ₹, lot size
- Conflict detection: if MA and RSI disagree on the same underlying, both signals are shown
  with source labels so you can decide

**Strategy used by the bot:** `CombinedStrategy(TrendFollowing + RSI)` with current config
params (fast MA 25, slow MA 75, RSI 14, oversold 25, overbought 65).

**Setup:**

1. Create a bot via [@BotFather](https://t.me/BotFather), get the token
2. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
3. Add as GitHub repository secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

```bash
# Run manually
set TELEGRAM_BOT_TOKEN=your_token
set TELEGRAM_CHAT_ID=your_chat_id
python src/telegram_notify.py
```

---

## Weekly Optimization Review by Email

GitHub Actions can replay the completed trading week every Saturday at 9:00 AM IST and email
a strategy scoreboard plus trade evidence. This is the feedback loop for tuning thresholds:
daily Telegram alerts propose trades; the weekly review shows which strategies actually paid,
which exits fired, and where IV/target/stop parameters need work.

```bash
# Generate files and send email when SMTP env vars are configured
python main.py weekly-report

# Generate local files only
python main.py weekly-report --no-email

# Review the week containing a specific date
python main.py weekly-report --as-of 2026-09-05
```

Outputs are saved under `reports/weekly/`:
- `weekly_review_<week>.html` — readable email/report
- `weekly_trades_<week>.csv` — closed-trade evidence for analysis
- `weekly_metrics_<week>.csv` — strategy-level scoreboard

Add these GitHub repository secrets to enable email:
- `SMTP_HOST`
- `SMTP_PORT` such as `587`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `WEEKLY_EMAIL_FROM`
- `WEEKLY_EMAIL_TO` as one or more comma-separated recipients
- `SMTP_TLS` optional, defaults to true

---

## Biweekly Stock Screener via Telegram

GitHub Actions runs both screening strategies every other Tuesday at 6:00 PM IST and sends
a summary of top matches via Telegram.

**What the bot sends:**
- Cheap to Moon strategy: count of matches + top 5 stocks
- Multibagger strategy: count of matches + top 5 stocks
- Total matches across both strategies
- Stock details: ticker, price, P/B, P/E ratios
- Execution timestamp

**Trigger:** Every other Tuesday at 6:00 PM IST (12:30 UTC)

**Performance:** Optimized for speed — screens all 2,404 NSE stocks in ~12-15 minutes 
(parallelized fetching with 6 workers on GitHub Actions).

**Manual trigger:**
```bash
# Run screener locally (default 4 parallel workers)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt
python main.py screen --strategy multibagger --universe nse_tickers_template.txt

# Customize parallel workers (2-8 recommended, more = faster but higher API load)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt --max-workers 6

# Or use GitHub Actions UI to manually trigger the workflow
```

**Setup:** Same as daily signals — requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets.

The workflow:
1. Downloads the latest market data and NSE stock universe
2. Runs Cheap to Moon screening against all 2,404 NSE stocks
3. Runs Multibagger screening against all 2,404 NSE stocks
4. Generates a formatted summary message
5. Sends results to your Telegram chat
6. Uploads CSV reports as GitHub Actions artifacts for download

---

## Performance Optimization

### Stock Screener Speed

The stock screener was optimized to handle 2,404 NSE stocks efficiently:

**Before:** 70 minutes
- Serial processing (1 stock at a time)
- 0.5s rate-limiting delay per stock
- No parallelization

**After:** 12-15 minutes (4-5x faster)
- **Parallel fetching** with ThreadPoolExecutor (configurable workers)
- **Thread-safe rate limiting** (300ms smart delay, not fixed 500ms)
- **Smart separation** of fetch and validation phases
- Fetch all metrics concurrently, then validate serially (fast)

**Configuration:**
```bash
# Default: 4 parallel workers
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt

# Faster: 6-8 workers (CI/CD friendly, higher API load)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt --max-workers 8

# Conservative: 2 workers (low resource usage, respectful of APIs)
python main.py screen --strategy cheap_to_moon --universe nse_tickers_template.txt --max-workers 2
```

**How it works:**
- Screener.in is rate-limited (300ms between requests) using thread-safe locks
- yfinance calls run in parallel (different API, no rate limit)
- Multiple threads fetch different stocks simultaneously
- Progress logged every ~10% completion instead of per-stock noise

---

## Configuration

All parameters live in `config.py`. Key settings:

### Capital & Risk

| Parameter               | Default    | Description                                      |
|-------------------------|------------|--------------------------------------------------|
| `STARTING_CAPITAL`      | ₹5,00,000  | Initial capital                                  |
| `RISK_PER_TRADE_PCT`    | 2%         | Capital at risk per trade                        |
| `MAX_OPEN_POSITIONS`    | 5          | Max concurrent open trades                       |
| `MAX_LOTS_PER_TRADE`    | 10         | Hard cap on lots per trade                       |
| `MAX_DRAWDOWN_HALT_PCT` | 15%        | Halt new trades if drawdown exceeds this         |

### Options & Execution

| Parameter                  | Default | Description                                   |
|----------------------------|---------|-----------------------------------------------|
| `BUY_STOP_LOSS_PCT`        | 40%     | Exit long if premium falls this much          |
| `BUY_TARGET_PCT`           | 200%    | Exit long if premium doubles (200%)           |
| `SELL_STOP_LOSS_PCT`       | 100%    | Exit short if premium hits 2× received        |
| `SELL_TARGET_PCT`          | 50%     | Exit short if premium decays 50%              |
| `SLIPPAGE_PCT`             | 1.5%    | Adverse slippage on entry and exit            |
| `RISK_FREE_RATE`           | 6.5%    | Annualised (91-day T-bill proxy)              |
| `DAYS_BEFORE_EXPIRY_EXIT`  | 1       | Close all positions N days before expiry      |

### Strategy Parameters

| Parameter               | Default | Description                                       |
|-------------------------|---------|---------------------------------------------------|
| `TREND_FAST_MA`         | 25      | Fast MA period                                    |
| `TREND_SLOW_MA`         | 75      | Slow MA period                                    |
| `RSI_PERIOD`            | 14      | RSI calculation period                            |
| `RSI_OVERSOLD`          | 25      | Buy CE when RSI recovers above this               |
| `RSI_OVERBOUGHT`        | 65      | Buy PE when RSI rolls below this                  |
| `MR_IV_PERCENTILE_ENTRY`| 70      | Short strangle entry IV percentile                |
| `MR_IV_PERCENTILE_EXIT` | 30      | Short strangle exit IV percentile                 |
| `ORB_BREAKOUT_PCT`      | 0.5%    | Close must be this % above/below open             |
| `ORB_GAP_MAX_PCT`       | 1.5%    | Skip if open gapped more than this from prev close|
| `STRADDLE_IV_ENTRY_PCT` | 30      | Buy straddle when IV percentile ≤ this            |
| `STRADDLE_IV_EXIT_PCT`  | 60      | Exit straddle when IV percentile ≥ this           |
| `VWAP_WINDOW`           | 20      | Rolling bars for VWAP calculation                 |
| `VWAP_STD_MULT`         | 1.5     | Standard deviation multiplier for VWAP bands      |
| `GAP_MIN_PCT`           | 0.5%    | Minimum gap % to consider fading                  |
| `GAP_MAX_PCT`           | 2.0%    | Maximum gap % — above this is news, don't fade    |
| `IC_IV_ENTRY_PCT`       | 60      | Iron condor entry IV percentile                   |
| `IC_BODY_DELTA`         | 0.25    | Delta for short body strikes                      |
| `IC_WING_DELTA`         | 0.10    | Delta for long wing strikes                       |
| `IC_ADX_CHOPPY_THRESHOLD`| 22.0   | Only enter iron condor when ADX < this (chop)     |

### Data

| Parameter          | Default                                    | Description                  |
|--------------------|--------------------------------------------|------------------------------|
| `BHAVCOPY_FOLDER`  | `data/bhavcopy`                            | NSE bhavcopy ZIP folder      |
| `BHAVCOPY_SYMBOL`  | `NIFTY`                                    | Symbol filter in bhavcopy    |
| `UNDERLYINGS`      | `^NSEI`, `^NSEBANK`, `NIFTY_FIN_SERVICE.NS`| Tickers to trade             |

---

## Performance Metrics

Every backtest, optimizer run, and explorer result reports these metrics:

| Metric               | Description                                                   |
|----------------------|---------------------------------------------------------------|
| Total Trades         | Number of closed trades                                       |
| Win Rate %           | % of trades with positive P&L                                |
| Total Return %       | Overall return on starting capital                            |
| CAGR %               | Compound Annual Growth Rate                                   |
| Avg / Median Trade % | Mean and median return per trade                              |
| Best / Worst Trade % | Single best and worst trade                                   |
| Avg Win / Avg Loss % | Mean return of winning and losing trades                      |
| Profit Factor        | Gross wins ÷ gross losses (>1.0 = profitable overall)        |
| Sharpe per Trade     | Mean trade return ÷ std dev of all trade returns              |
| Sortino per Trade    | Mean trade return ÷ std dev of losing trades (downside only)  |
| Max Drawdown %       | Largest peak-to-trough equity decline                         |
| Avg Held Days        | Average trade duration in calendar days                       |
| Avg Entry Delta      | Mean absolute delta of options at entry                       |
| Avg Theta %/day      | Mean daily theta decay as % of premium paid                   |

---

## HTML Dashboard Report

Every backtest saves a self-contained HTML report to `reports/`. Sections:

- **Summary cards** — all metrics, color-coded green/red
- **Equity curve** — interactive Plotly chart with drawdown shading
- **Monthly returns heatmap** — calendar grid of monthly P&L %
- **Trade log table** — sortable, filterable, paginated, green/red rows
- **Win/Loss distribution** — histogram with avg win/loss lines
- **Trade P&L bar chart** — per-trade bars with cumulative P&L line
- **Strategy parameters panel** — all config values embedded for reproducibility

All JS, CSS, and Plotly data is embedded inline — no internet required to open.

---

## Recent Improvements

- Short option entries now use sell-side transaction costs, so short premium strategies include
  the correct STT/stamp-duty treatment at entry.
- Strategies now receive an `on_trade_closed(trade)` lifecycle callback after the backtester exits
  a position via stop-loss, target, expiry, signal, or forced backtest close.
- Stateful strategies reset local position flags on close, preventing stale `long_ce` / `long_pe`
  state from blocking future valid signals.
- `CombinedStrategy` routes close callbacks back to the child strategy that opened the trade using
  `trade.entry_meta["source_strategy"]`.
- Signal validation now raises `ValueError` instead of relying on Python `assert`, so validation
  still runs under optimized Python.

---

## Adding a New Strategy

1. Create `src/algo_trading/strategies/my_strategy.py`
2. Inherit from `BaseStrategy`, implement `name` property and `generate_signals(data, vix, current_date)`
3. Return a list of `Signal` objects (entry or exit)
4. Register with `@register_strategy("my_key")`
5. Import/export the strategy from `src/algo_trading/strategies/__init__.py`
6. Add config defaults to `config/strategy_params.py` if the strategy needs tunable parameters

**Signal schema:**

```python
Signal(
    date        = current_date,       # date of signal
    underlying  = "^NSEI",            # Yahoo Finance ticker
    direction   = "long",             # "long" (buy option) | "short" (sell option)
    option_type = "CE",               # "CE" | "PE"
    strike      = 0.0,                # 0 = ATM; backtester resolves it
    expiry      = next_expiry(...),   # expiry date
    signal_type = "entry",            # "entry" | "exit"
    meta        = {"trigger": "..."}  # any metadata for reports
)
```

For multi-leg strategies (straddle, iron condor): emit multiple `Signal` objects in one
`generate_signals()` call. The backtester handles each as an independent `Trade`.

If the strategy stores local position state such as `_prev_signal`, `_entry_date`, `_pending`, or
`_cross_counter`, the base `on_trade_closed(trade)` hook will reset those fields when a matching
trade closes. Override `on_trade_closed()` only when the strategy has custom multi-leg state that
cannot be reset by the default hook.

---

## Brokerage & Tax Model

All P&L deducts real transaction costs (Groww flat-fee model):

| Cost             | Rate                                           |
|------------------|------------------------------------------------|
| Brokerage        | ₹20 flat per order (or 0.05%, whichever lower) |
| STT              | 0.125% on sell side (options)                  |
| Exchange charges | 0.053% of premium                              |
| GST              | 18% on brokerage + exchange charges            |
| SEBI charges     | ₹10 per crore turnover                         |
| Stamp duty       | 0.003% on buy side                             |

---

## Project Structure

```
algo-trading/
├── .env.example                     # Environment variables template (Telegram keys)
├── config/                          # Modular configuration package
│   ├── __init__.py                  # Re-exports all settings
│   ├── settings.py                  # Capital, lot sizes, brokerage & risk parameters
│   └── strategy_params.py           # Default hyperparameters per strategy
├── config.py                        # Root backward-compatible config proxy
├── pyproject.toml                   # Standard package configuration (pip install -e .)
├── requirements.txt                 # Python dependencies
├── main.py                          # Unified CLI entry point
├── strategy_explorer.py             # Strategy combination tester & ranker
├── download_bhavcopy.py             # Bhavcopy download helper
├── fetch_nse_symbols.py             # NSE ticker fetcher
├── test.py                          # Legacy experimental scratchpad
│
├── src/
│   └── algo_trading/                # Canonical Python package namespace
│       ├── core/                    # Simulation & mathematical engines
│       │   ├── backtester.py        # Event-driven backtest engine & Trade dataclass
│       │   ├── pricing.py           # Black-Scholes pricing + Greeks + expiry helpers
│       │   └── risk_manager.py      # Position sizing + SL/target + drawdown gating
│       ├── data/                    # Data ingestion & market data loaders
│       │   ├── ingestion.py         # YFinance fetching + bhavcopy loader + ChainLookup
│       │   ├── bhavcopy.py          # NSE bhavcopy downloader
│       │   └── screener_fetcher.py  # Fundamental metrics scraper
│       ├── strategies/              # Pluggable strategy registry
│       │   ├── __init__.py          # Strategy registry & auto-discovery
│       │   ├── base.py              # BaseStrategy, Signal, and @register_strategy
│       │   ├── trend_following.py   # MA Crossover (ADX, confirm, direction, time-stop)
│       │   ├── rsi_strategy.py      # RSI Reversal (Wilder smoothing)
│       │   ├── bollinger_band_strategy.py # BB reversion with VIX regime filter
│       │   ├── confluence_strategy.py   # MA + RSI Agreement (both must confirm)
│       │   ├── mean_reversion.py    # Short Strangle on High IV percentile
│       │   ├── combined_strategy.py # Meta: merges signals from N child strategies
│       │   ├── inverse_strategy.py  # Meta: flips CE↔PE on any strategy
│       │   ├── orb_strategy.py      # Opening Range Breakout (daily proxy)
│       │   ├── long_straddle.py     # Long Straddle/Strangle on Low IV
│       │   ├── vwap_reversion.py    # VWAP ± σ-band reversion or breakout
│       │   ├── gap_fade.py          # Fade opening gaps with no follow-through
│       │   └── iron_condor.py       # Delta-neutral Iron Condor (chop regime)
│       ├── optimization/            # Search & parameter optimization
│       │   ├── grid_search.py       # Level 1: Grid search (16× speedup via replay)
│       │   └── walk_forward.py      # Level 2: Walk-forward with overfitting detection
│       ├── screener/                # Stock screening engine
│       │   └── stock_screener.py    # Multi-strategy equity screener
│       ├── reporting/               # Analytics & dashboard rendering
│       │   ├── metrics.py           # 15+ performance metrics + console summary
│       │   └── html_generator.py    # HTML dashboard generator (Jinja2 + Plotly)
│       └── notifications/           # Automated alert bots
│           ├── telegram.py          # Daily signals via Telegram Bot API
│           └── screener_bot.py      # Biweekly stock screening results via Telegram
│
├── scripts/                         # Standalone runner scripts
│   ├── download_bhavcopy.py
│   ├── fetch_nse_symbols.py
│   └── strategy_explorer.py
│
├── tests/                           # Pytest automated test suite
│   ├── conftest.py                  # Fixtures & mock market data
│   ├── test_pricing.py              # Black-Scholes & Greeks tests
│   ├── test_strategies.py           # Strategy registry & signal tests
│   └── test_backtester.py           # Backtest execution & PnL verification
│
├── templates/
│   └── report_template.html         # Jinja2 HTML template
├── .github/
│   └── workflows/
│       ├── daily_signals.yml        # GitHub Actions: daily Telegram signals (weekdays 4:15 PM IST)
│       └── biweekly_screener.yml    # GitHub Actions: biweekly stock screening (Tuesdays 6:00 PM IST)
├── data/
│   ├── bhavcopy/                    # NSE F&O bhavcopy ZIP files (~2,300 files)
│   ├── cache/                       # Intermediate data cache
│   └── raw/                         # Downloaded OHLCV CSVs
├── reports/                         # HTML reports, trade CSVs, explorer/optimizer results
└── signals/                         # Daily signal output files
```

---

## Disclaimer

- Options pricing falls back to Black-Scholes with India VIX as IV proxy when bhavcopy is
  unavailable for a given date
- Backtest results are indicative only — not a guarantee of future performance
- This is not financial advice. All trades are your own decision
