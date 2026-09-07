# Algo Trading — NSE Options System

Python-based backtesting, signal generation, strategy exploration, and parameter optimization
for NSE index options (India). Supports directional, volatility, mean-reversion, and
delta-neutral strategies. Generates interactive HTML reports with full trade logs and charts.
The daily GitHub Actions workflow generates an EOD watchlist report. Intraday entries
require actual candles, fresh option quotes, and portfolio validation; no trade is forced.

**Options engine update:** exact-contract pricing, shared spread risk, mark-to-market equity,
nested validation, four vertical-spread strategies and timestamped intraday replay are documented
in [Options engine guide](docs/options_engine.md). The daily report adds two-stage confirmation,
contract-IV history checks, expiry-aware eligibility, and portfolio-aware sizing.
Existing sample results predate these fixes
and should be regenerated. Research fallback prices and historical specification proxies are labeled.

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv-options
.venv-options\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

To run unit and regression tests with the options environment:

```powershell
.venv-options\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp reports/test-readme-example
```

Choose a fresh `reports/` subdirectory for each run. The signal-quality implementation
passed 129 tests; these regression checks do not establish trading accuracy.

If `python` is not found or `.venv-options\Scripts\python.exe` points to an inaccessible interpreter,
delete and recreate the virtual environment with the same install commands above.

### 2. Run your first backtest

```bash
python main.py backtest --strategy combined --ticker ^NSEI
```

This downloads Nifty 50 + India VIX data (cached after first run), runs the MA crossover + RSI
combined strategy, simulates options trades, prints a metrics summary, and saves an HTML report
to `reports/`. Open it in any browser.

### 3. Generate today's daily options report

```powershell
.venv-options\Scripts\python.exe main.py daily-report
```

Read `reports/daily-signals/YYYY-MM-DD/report.md`. The report separates confirmed
entries, primary setups awaiting triggers/data, alternatives, and no-trade reasons.
EOD-only generation never confirms an entry. See [Daily options reports](#daily-options-reports)
for replay, live-provider setup and evaluation.

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
| `mean_rev`    | Short Strangle on High IV         | Legacy VIX regime percentile → CE + PE candidate      |
| `bb`          | Bollinger Band Reversion          | Price touches band edge, reverts to mean      |
| `iron_condor` | Iron Condor (delta-neutral)       | Legacy VIX regime + choppy ADX → 4-leg candidate   |

### Long Volatility (buy vol cheap, profit from expansion)

| Key           | Strategy                          | Signal source                                 |
|---------------|-----------------------------------|-----------------------------------------------|
| `straddle`    | Long ATM Straddle                 | Legacy low VIX percentile → ATM CE + PE candidate   |
| `strangle`    | Long OTM Strangle                 | Legacy low VIX percentile → OTM CE + PE candidate   |

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

**Daily report:** evaluates the existing EOD strategy registry and adds conditional ORB/session
VWAP plans. Daily ORB/VWAP proxies are excluded from this report's entry path. The legacy
`signals` command remains available for EOD research; it is not intraday confirmation.
Legacy volatility strategy classes use India VIX regime percentiles. The new daily report
independently gates volatility setups on selected-contract IV history.

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

## Parameter Optimization

```bash
python main.py optimize --quick          # full execution for each combination
python main.py optimize --quick --top 1  # nested train/test selection + reserved final year
```

Each risk configuration replays the complete event path with fresh strategy state.
Results are written to CSV and never applied automatically to configuration.
Walk-forward searches within each training fold rather than preselecting parameters
on the whole dataset. See the [options engine guide](docs/options_engine.md#optimization)
for data requirements, holdout semantics and limitations.

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
This improves option-price realism for each configured index. The backtester maps
Yahoo underlyings to NSE option symbols with `BHAVCOPY_SYMBOLS`, currently:
`^NSEI -> NIFTY`, `^NSEBANK -> BANKNIFTY`, and
`NIFTY_FIN_SERVICE.NS -> FINNIFTY`. Each symbol gets its own cached chain lookup,
so BANKNIFTY and FINNIFTY trades are never priced from NIFTY contracts.

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

The `ChainLookup` class provides fast `(date, expiry, strike, option_type)` premium lookup
plus open/close/OI/volume metadata. Backtests use next-open option `OPEN` prices when
available and fall back to close/settle, then Black-Scholes, with the price source recorded
on each trade.

### Backtest Execution Semantics

Daily strategy signals are generated after a completed market bar. By default
`EXECUTION_TIMING = "next_open"`:

- A signal generated on day T is entered on the next valid trading day at the option open.
- Trade records include both `signal_date` and `entry_date`.
- If the next trading day is outside the backtest window, the signal remains unexecuted and
  is not counted as a realized trade.
- `same_day_close` remains available only as an explicit legacy/test execution mode.

Multi-leg strategies now execute as one structure. Straddles, strangles, short strangles,
and iron condors share `group_id` and `structure_type`; max open positions, stop-loss,
target, and capital updates are applied at the structure level. CSV/report rows still include
leg-level details for auditability.

---

## Daily Options Reports

The workflow in this branch runs `daily-report` on weekdays at **19:30 IST** and uploads
report artifacts instead of sending Telegram messages. Scheduled behavior changes only
after deployment to the default branch. Local report generation sends no messages or orders.

### Evening watchlist and intraday confirmation

1. Validate the exact EOD session's spot candles and NSE option archive. Missing data is
   reported explicitly, separately from a market with no strategy signals.
2. Select each underlying's listed expiry at intended entry. Expiry-day entries default
   off; policy settings control their cutoff and minimum remaining hours.
3. Rank eligible setups using observed triggers, spread width and deterministic tie breaks.
   Show one primary per underlying and alternatives separately. Ranking is a heuristic,
   not a predicted probability of success.
4. Confirm using completed intraday candles and fresh exact-contract quotes. ORB requires
   the actual opening range; session VWAP requires actual traded volume. Index candles
   with absent volume cannot confirm VWAP.

Contract IV comes from the selected option premium, with comparisons to prior sessions
of the same underlying, option type, tenor and nearby moneyness. India VIX is a regime
feature, **not the selected contract's IV percentile**. Fewer than 20 comparable historical
sessions leave volatility setups blocked; future observations never enter the baseline.

Sizing distinguishes **lot size, lots and units**. It checks available cash, existing
portfolio exposure, fees, spread, quote age and displayed depth. Duplicate contract
exposure is aggregated before allocation. Paired legs retain equal quantities and one
whole-trade budget. EOD sizing is an estimate; missing specifications leave quantities
unavailable. Confirmation requires a verified portfolio snapshot no more than 60 seconds old.

### Commands and outputs

```powershell
# Generate the EOD report; the date can be omitted for today.
.venv-options\Scripts\python.exe main.py daily-report --as-of 2026-09-07

# Offline status report without fetching market data.
.venv-options\Scripts\python.exe main.py daily-report --as-of 2026-09-07 --offline

# Confirm a saved EOD watchlist against archived intraday observations.
.venv-options\Scripts\python.exe main.py daily-report --as-of 2026-09-07 --entry-at 2026-09-08T09:32:00+05:30 --watchlist reports/daily-signals/2026-09-07/watchlist.json --bars '^NSEI=data/nifty_minutes.csv' --quotes data/option_quotes.csv --contract-master data/contracts.csv --portfolio data/portfolio.json --iv-history data/iv_history.csv --output reports/confirmation/2026-09-08
```

These commands require your own data files where paths are supplied. Repeat `--bars`
for additional underlyings. The default next-weekday entry date is provisional, not an
exchange holiday calendar. Replay timestamps must include timezone offsets and bars
must represent completed intervals.

| Output | Purpose |
| --- | --- |
| `report.md` | Readable daily watchlist, confirmation status and no-trade reasons |
| `report.json` | Structured decisions, sizing, exposure and report-coverage fields |
| `watchlist.json` | Dated, validated EOD candidates for later confirmation |
| `iv_observations.csv` | Contract IV observations to archive for future comparisons |

Without `--watchlist`, `--offline` produces a missing-data report. Saved watchlists must
match `--as-of`. See the [options engine guide](docs/options_engine.md#two-stage-daily-signal-quality)
for portfolio/history schemas and `--policy` settings.

### Provider requirements

The read-only **Upstox** adapter was selected for the free-data preference. It uses the
documented intraday-candle and full-quote APIs; the integration was verified with mocked
responses, not an authenticated live account. Supply `UPSTOX_ACCESS_TOKEN` securely in
the environment, official instrument-key mappings, contract specifications and a fresh
portfolio snapshot. Do not put credentials in reports or source control.

Use `--provider upstox --instruments data/upstox_instruments.json` with the saved
watchlist, contract master and portfolio. Omit `--entry-at` for live Upstox: the decision
timestamp is captured after data acquisition. Missing or stale observations cannot
confirm an entry. Historical confirmation uses CSV replay, not current API responses
relabeled as historical data.

Bhavcopy supplies **EOD premiums/OI/volume**, not intraday bid/ask quotes. Comparable IV
history must be archived separately and passed with `--iv-history`; it is not fabricated
from India VIX. See [verified provider capabilities](docs/options_engine.md#verified-provider-capabilities)
for official API references, instrument mapping and remaining limitations.

### Chronological evaluation and coverage

```powershell
.venv-options\Scripts\python.exe main.py evaluate-signals --ticker '^NSEI' --bars data/nifty_minutes.csv --quotes data/option_quotes.csv --contract-master data/contracts.csv --train-sessions 60 --test-sessions 20 --holdout-sessions 20 --report-journal reports/daily-signals --output reports/quality-evaluation
```

The runner selects ORB/VWAP and expiry-day policies using expanding training windows,
evaluates subsequent unseen sessions, and reserves a separate final holdout. It writes
`evaluation.json` with net expectancy, trade count, win rate and drawdown, broken down
by strategy, underlying, entry expiry distance and prior-session market conditions.
Breakdowns use realized subgroup P&L drawdown; fold/holdout summaries also include
observed intraday equity drawdown. This runner does not evaluate every EOD strategy.

Daily report coverage and actionable-report frequency are counted separately from the
report journal. Their denominator is observed bar sessions, so a complete session
archive is required to assess coverage. Synthetic replay/regression results establish
execution behavior only. **No improved accuracy or profitability is claimed.**

### Storage and notifications

- `data/signal-bhavcopy/`: separate 14-day option cache, with one cache snapshot key per IST date.
- Daily reports and signal-data diagnostics: seven-day workflow artifact retention.
- `data/bhavcopy/`: full historical archive, untouched by signal-cache cleanup.

The daily workflow no longer invokes the Telegram sender. Legacy Telegram functions,
the separate stock-screener notifications, and weekly email remain separate features.

---

## Weekly Optimization Review by Email

GitHub Actions can replay the completed trading week every Saturday at 9:00 AM IST and email
a strategy scoreboard plus trade evidence. This is the feedback loop for tuning thresholds:
daily reports identify watchlist setups; the weekly review shows simulated strategy outcomes,
which exits fired, and where IV/target/stop parameters need work. Friday signals that would
execute after the reviewed week are left pending and are not counted as realized trades.

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

Weekly HTML and CSV outputs include structure metadata (`group_id`, `structure_type`,
leg labels), price source, VIX source, and regime buckets. The report separately counts
fallback/stale-VIX trades and ranks performance by regime with insufficient-sample warnings.

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

**Setup:** The separate stock-screener workflow requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repository secrets.

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
| `EXECUTION_TIMING`         | `next_open` | Execute after-close signals at next open  |
| `BHAVCOPY_SYMBOLS`         | per index | Map Yahoo tickers to NSE option symbols    |
| `VIX_STALE_DAYS`           | 3       | Mark cached VIX stale after this many days    |
| `MIN_OPTION_VOLUME`        | per index | Live signal volume threshold proxy          |
| `MIN_OPTION_OI`            | per index | Live signal OI threshold proxy              |

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
| `BHAVCOPY_SYMBOL`  | `NIFTY`                                    | Legacy default symbol filter |
| `BHAVCOPY_SYMBOLS` | `NIFTY`, `BANKNIFTY`, `FINNIFTY`           | Per-underlying chain mapping |
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

- Two-stage daily reports now separate EOD watchlists from intraday confirmation, with
  contract-IV history gates, expiry-aware rules, ranked alternatives and shared portfolio sizing.
- Read-only Upstox observations and offline CSV replay support the confirmation boundary.
- Chronological ORB/VWAP evaluation includes separate holdout and report-coverage metrics.

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
- Backtests now default to next-open execution and record `signal_date` separately from
  `entry_date`.
- Multi-leg option strategies are managed as one position for max-open-position, exit, and
  capital accounting while preserving leg-level trade rows.
- Live Telegram signals fail closed on fallback/stale VIX for volatility strategies and validate
  expiry, strike, premium, OI, and volume before alerting.
- Weekly review now includes regime performance, fallback/stale VIX counts, and grouped
  structure metadata.

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
    group_id    = "optional-group",   # shared by multi-leg structures
    structure_type = "single",        # single | straddle | strangle | short_strangle | iron_condor
    meta        = {"trigger": "..."}  # any metadata for reports
)
```

For multi-leg strategies (straddle, iron condor): emit multiple `Signal` objects in one
`generate_signals()` call with the same `group_id` and `structure_type`. The backtester
opens one grouped position, applies structure-level exits, and still writes one `Trade`
row per leg.

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
│       │   ├── volatility.py        # Contract IV comparisons and entry-time expiry distance
│       │   ├── intraday.py          # Quote-driven intraday execution replay
│       │   └── risk_manager.py      # Position sizing + SL/target + drawdown gating
│       ├── data/                    # Data ingestion & market data loaders
│       │   ├── ingestion.py         # YFinance fetching + bhavcopy loader + ChainLookup
│       │   ├── bhavcopy.py          # NSE bhavcopy downloader
│       │   ├── providers.py         # Read-only Upstox and offline replay interfaces
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
│       │   ├── grid_search.py       # Full execution grid search
│       │   └── walk_forward.py      # Nested walk-forward and final holdout
│       ├── screener/                # Stock screening engine
│       │   └── stock_screener.py    # Multi-strategy equity screener
│       ├── reporting/               # Analytics & dashboard rendering
│       │   ├── metrics.py           # 15+ performance metrics + console summary
│       │   ├── daily_signals.py     # EOD watchlists, confirmation and shared sizing
│       │   ├── signal_evaluation.py # Chronological evaluation and report coverage
│       │   └── html_generator.py    # HTML dashboard generator (Jinja2 + Plotly)
│       └── notifications/           # Automated alert bots
│           ├── telegram.py          # Legacy EOD formatting and Telegram sender
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
│       ├── daily_signals.yml        # Daily report artifacts (weekdays 7:30 PM IST)
│       └── biweekly_screener.yml    # GitHub Actions: biweekly stock screening (Tuesdays 6:00 PM IST)
├── data/
│   ├── bhavcopy/                    # NSE F&O bhavcopy ZIP files (~2,300 files)
│   ├── signal-bhavcopy/             # Separate 14-day daily-signal option cache
│   ├── cache/                       # Intermediate data cache
│   └── raw/                         # Downloaded OHLCV CSVs
├── reports/                         # HTML reports, trade CSVs, explorer/optimizer results
└── signals/                         # Daily signal output files
```

---

## Disclaimer

- Daily-report confirmation requires actual intraday data and validated portfolio inputs;
  missing live data remains unconfirmed, and EOD quantities are estimates
- Historical/research pricing behavior below does not supply live execution quotes

- Options pricing falls back to Black-Scholes with India VIX as IV proxy when bhavcopy is
  unavailable for a given date; fallback usage is tagged in reports
- Live volatility alerts are suppressed when VIX is fallback/stale, but backtests may include
  tagged fallback/stale-VIX trades for historical continuity
- Backtest results are indicative only — not a guarantee of future performance
- This is not financial advice. All trades are your own decision
