# Options engine: research, validation and intraday replay

## Daily backtests

```powershell
python main.py backtest --strategy bull_call_spread --ticker '^NSEI'
python main.py backtest --strategy iron_condor --ticker '^NSEI' --pricing-mode strict --contract-master data/contracts.csv --fee-schedule data/fees.csv
```

Research mode permits Black-Scholes fallback. Strict mode requires exact contract
quotes and archived contract specifications. It rejects missing opening prices,
missing prior-day volume/OI, incomplete structures, unaffordable lots and unsupported
physical settlement. A missing valuation for an already held strict-mode contract
fails the run rather than deleting the position or inventing a closing price.

A contract's underlying, strike, option type and expiry remain fixed after entry.
Opening trades use the prior observation of VIX, not the entry day's closing VIX.
Strategy-generated exits execute at the next open. Daily stop/target checks are
close-observation simulations and cannot establish whether an intraday stop would
have filled. Strict mode does not turn bhavcopy OHLC into synchronized bid/ask quotes.
The original `orb` and `vwap_rev` daily strategies remain proxies; use `intraday`
for timestamped session signals.

All legs of a supported structure use the same lot count. Sizing uses the expiry
payoff's maximum loss (including entry fees), free capital and aggregate risk limits.
Equal-ratio iron condors and verticals are validated for protective wing orientation.
One lot is rejected if it exceeds the budget. Existing positions are marked at the open before new entries are sized, so
overnight losses affect the budget. Closed-position P&L includes both
entry and exit costs. Open equity includes estimated liquidation costs. Final equity
is reconciled after forced exits. Maximum loss is an expiry payoff bound, not a
promise about intermediary mark-to-market losses or execution costs.

Uncovered shorts are disabled by default. Enabling `ALLOW_UNCOVERED_SHORTS` also
requires a `margin_provider(legs, entry_date)` returning positive finite one-lot
`(required_capital, stress_risk)` values. No exchange margin model is fabricated.
For defined-risk structures the collateral reservation is conservative payoff-based
capital, not a broker SPAN margin quote. Portfolio risk/margin utilization limits,
a daily entry loss limit, and optional delta/gamma/vega/theta limits are configured
in `config/settings.py`. Drawdown and Greek breaches halt additional entries; the
daily engine does not promise immediate intraday liquidation.

## Archived specifications and fees

Contract master CSV columns:

```csv
underlying,expiry,lot_size,effective_from,effective_to,settlement
```

Dates are ISO `YYYY-MM-DD`; underlying uses the project's ticker (e.g. `^NSEI`).
Each row describes a specific expiry over an inclusive effective date interval.
`settlement` must be `cash`. Preserve real listed expiry dates, including holiday
adjustments, from archived exchange/broker instrument masters. Overlapping records
are rejected. Use effective intervals when contract quantities change. The engine
freezes each entered leg's quantity; it does not model mid-position corporate actions.

Fee CSV columns (all required):

```csv
effective_from,effective_to,BROKERAGE_PER_ORDER,BROKERAGE_MAX_PCT,STT_SELL_PCT,STT_BUY_PCT,EXCHANGE_CHARGE_PCT,GST_PCT,SEBI_CHARGE_PER_CR,STAMP_DUTY_BUY_PCT
```

Rates ending in `_PCT` are percentages, not decimal fractions. With a fee schedule
configured, gaps or overlapping intervals raise an error. Without it, the existing
current-rate estimate remains labeled `current_rates_proxy`. This flat-order fee
formula does not model exercise/physical-delivery taxes or every broker plan.

No historical instrument records or paid intraday quotes are bundled. Obtain the
appropriate archived data before treating a result as market-data validation.
Reference sources: [NSE contract information](https://www.nseindia.com/static/products-services/equity-derivatives-contract-information),
[NSE expiry transition circular](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAOP68747.pdf),
[NSE lot-size revision circular](https://nsearchives.nseindia.com/content/circulars/FAOP64672.pdf).

## IV, exposures and reports

Observed premiums are inverted for European Black-Scholes IV when a valid solution
exists. Greeks use that IV, with an explicitly labeled VIX proxy if inversion fails.
`ChainLookup.volatility_surface(date, spot)` returns per-contract IV and Greeks by
strike and expiry for skew/term-structure inspection. This is not a calibrated
arbitrage-free surface and does not account for dividend/forward-basis estimation.
India VIX remains a regime feature, not a substitute for contract-level IV.

Daily equity CSVs include unrealized P&L, reserved capital, utilization, signed
portfolio Greeks and modeled spot/volatility stress losses. Stress scenarios are
specified in settings; they are scenario estimates, not loss guarantees.

HTML reports count complete positions for win rate/profit factor and use daily
portfolio returns for Sharpe/Sortino. Individual legs remain in the trade log.
Sibling files contain equity, entry DTE/IV/structure breakdowns and JSON diagnostics
for pricing sources, current-setting proxies and rejected entries. Pricing source
counts describe quote requests, including rejected candidates, not only filled trades.

## Defined-risk strategies

`bull_call_spread`, `bear_put_spread`, `bull_put_spread`, `bear_call_spread` express
MA directional signals as two-leg verticals. Python construction accepts
`width_steps`, `fast_ma`, and `slow_ma`. They use the same exact-contract validation,
shared sizing, fee calculation and group exits as iron condors. These are research
candidates; adding a spread does not establish profitability.

## Optimization

```powershell
python main.py optimize --quick
python main.py optimize --quick --top 1
```

Every complete MA/RSI/stop/target combination runs a fresh strategy and execution
simulation. This is slower than the old terminal-price clipping shortcut, but models
changed exits, capital availability and subsequent signals. Results never rewrite
configuration. Lookups are isolated per underlying; risk settings are passed to the
engine rather than patched into a separate configuration module.

`--top N` runs nested chronological validation directly: search a fixed grid only
inside each training window, then evaluate its top N candidates in the following
test window. Indicators retain earlier warmup history; orders remain restricted to
the test dates. The final year is reserved as a holdout and excluded from all preceding
training and test selection. The final training window selects the holdout candidate.
Failures propagate; no-trade folds are explicitly reported. Comparing holdout results
and then changing the grid consumes the holdout; reserve new unseen data afterward.

## Intraday replay

```powershell
python main.py intraday --strategy orb --ticker '^NSEI' --bars data/index_bars.csv --quotes data/option_quotes.csv --contract-master data/contracts.csv
```

Bars: `timestamp,Open,High,Low,Close,Volume`.
Quotes: `timestamp,underlying,expiry,strike,option_type,bid,ask,bid_size,ask_size`.
Timestamps must include UTC offsets, e.g. `2026-09-04T09:20:00+05:30`, and bars must
represent completed intervals. Quote sizes are underlying units, not lots.
Supply meaningful traded volume for VWAP; an index series with absent volume does
not provide session VWAP. No vendor endpoint, access token or quote data is invented.

ORB freezes the first 15 minutes; `--strategy vwap` uses a VWAP that resets each
session. A signal can fill only at the following observed bar timestamp, using a
quote no more than 30 seconds old. Buys use ask; sells use bid. Entry limits enforce
spread width, displayed size and capital. The selected contract remains fixed.
The runner supports one underlying, one long-option position, and no overnight carry.
It exits at/after 15:20 or the session's last available bar; truncated sessions are
therefore closed at the final observation, not extrapolated to the market close.
A missing held-contract quote or inadequate required exit depth fails the replay
instead of inventing a fill. Stops are evaluated at observations; intra-bar paths,
partial fills, queue priority and broker routing are not simulated.

Outputs: `reports/intraday/trades.csv`, `equity.csv`, `rejections.csv`.

## Verification

```powershell
.\.venv-options\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp reports/test-fresh
```

Use a fresh scratch directory if Windows has locked an old pytest directory. Tests
use explicit synthetic fixtures; they validate accounting/execution invariants and
data boundaries, not strategy returns on live market data. No broker orders are sent.

Directional inverse wrappers preserve buy/sell side; multi-leg inversion requires a dedicated strategy.


## Daily alert data preparation

The scheduled job runs at 19:30 IST on weekdays to allow more time for EOD data.
Generation fetches the exact IST session's UDiFF archive, with at most three attempts,
checks the internal CSV trade date, and loads each configured index separately.
Neither earlier-session bhavcopy nor synthetic premiums satisfy alert validation.
Publication is still checked at runtime; the later schedule is not a guarantee.

Missing-data alerts explicitly say that candidates could not be validated. They do
not claim there were no strategy signals and omit trade-entry/GTT instructions.
A genuine no-signal result is reported separately. Stale spot candles are also blocked.
Diagnostics are saved under `reports/signal-data/` and uploaded by the workflow.
The supported file names include legacy `foDDMMMYYYYbhav.csv.zip`, local
`fo_bhavcopy_YYYYMMDD.zip`, and NSE UDiFF final ZIP names.

To fetch/check a specific day's archive without sending a message:

```powershell
python -m algo_trading.data.signal_data --as-of 2026-09-07
```

Source: [NSE derivatives reports](https://www.nseindia.com/all-reports-derivatives).
The corrected UDiFF URL was smoke-tested with the 2024-07-09 public archive.
Changes to the local workflow only affect scheduled runs after deployment to the
repository's default branch; generating a local report does not deploy or send it.


### Free-plan storage controls

The daily workflow uses `data/signal-bhavcopy/` through `SIGNAL_BHAVCOPY_FOLDER`.
It prunes archives older than 14 calendar days in that dedicated cache only.
`data/bhavcopy/` remains the separate, untouched local backtesting archive.
Cache snapshots use one key per IST date rather than one per run, and the new
`option-recent-v1` prefix does not restore older unbounded cache snapshots.
Multiple date snapshots can still occupy storage; each snapshot has a bounded
14-day payload. Existing remote caches are not deleted by this local change.
Daily signal-data diagnostic artifacts expire after 7 days.

## Two-stage daily signal quality

`daily-report` is the daily workflow's report command. It writes Markdown, JSON,
validated `watchlist.json`, and per-contract `iv_observations.csv`. It does not call
Telegram, email, or order endpoints. The workflow uploads the report for seven days;
merging/deploying is still required to change scheduled behavior.

```powershell
.venv-options\Scripts\python.exe main.py daily-report --as-of 2026-09-07
.venv-options\Scripts\python.exe main.py daily-report --as-of 2026-09-07 --entry-at 2026-09-08T09:32:00+05:30 --watchlist reports/daily-signals/2026-09-07/watchlist.json --bars '^NSEI=data/nifty_minutes.csv' --quotes data/option_quotes.csv --contract-master data/contracts.csv --portfolio data/portfolio.json --iv-history data/iv_history.csv --output reports/confirmation/2026-09-08
```

The next weekday default is a provisional planning date, **not an exchange holiday
calendar**. Supply the actual intended session with `--entry-at`; confirmation also
requires actual session candles. The saved watchlist date must match `--as-of`.
`--offline` avoids downloading EOD data. With no saved watchlist it produces a
missing-data report rather than inventing setups.

EOD directional strategies provide context. Daily ORB/VWAP proxies are excluded from
this path. ORB plans require a complete one-minute opening range and a subsequent
completed breakout candle; session VWAP plans require complete one-minute bars with
actual traded volume. An index with zero volume cannot confirm VWAP. Do not attach
futures volume to index prices; futures-based VWAP needs an explicit instrument and
basis model, which is not implemented here. Directional EOD ideas require a completed
intraday close beyond the EOD close in their direction. Volatility structures require
contract IV thresholds on every leg and fresh intraday spot. These are explicit
heuristic confirmation rules, not established sources of predictive accuracy.

The report separates confirmed entries, primary setups awaiting triggers/data, and
alternatives/ineligible setups. It never requires a daily trade. Missing option
specifications leave the watchlist visible but quantities unavailable. Eligible
setups rank before blocked ones, then observed triggers, smaller spreads, and stable
strategy-name tie breaks. Duplicate contract structures merge supporting strategy
labels when they share a trigger family; ORB and VWAP remain separate alternatives.
One primary is allocated per underlying, with shared portfolio reservations across
underlyings. Ranking is a transparent heuristic, not a probability or accuracy score.

Contract expiry comes from the underlying's current listed chain and is filtered at
intended entry. Expiry-day trading defaults off. A policy JSON supplied with `--policy`
can override `allow_expiry_day`, `expiry_day_cutoff` (IST, default `12:00`),
`min_hours_to_expiry` (2), `max_quote_age_seconds` (30), `max_bar_age_seconds` (90),
`max_spread_pct` (5), `min_iv_sessions` (20), `low_iv_percentile` (30), and
`high_iv_percentile` (70). Actual expiry-day performance is broken out separately from
other tenors in chronological evaluation.

IV is inverted from the selected contract's EOD premium or live bid/ask midpoint,
using this contract's remaining time. Historical comparisons use the same underlying,
option type, tenor bucket (expiry day, 1–7, 8–30, 31+ days), and strike/spot ratio within
0.02. One median per prior IST session avoids counting ticks as independent history;
at most 252 prior sessions are used. Today/future observations cannot enter the
percentile baseline. IV history CSV columns are:

```csv
timestamp,underlying,option_type,dte,moneyness,iv
```

IV is a decimal (0.15 = 15%); timestamps include offsets; `dte` is fractional days and
`moneyness` is strike/spot at observation. Archive the report's `iv_observations.csv`
into a sourced persistent history outside the short-lived bhavcopy cache, then pass
that history with `--iv-history`. No historical IV observations are fabricated or
reconstructed from future data. Until 20 comparable prior sessions exist, volatility
setups are blocked with an explicit reason. India VIX remains only a regime feature.
The Black-Scholes inversion remains a model with rate/dividend/forward-basis limitations;
it is not a calibrated arbitrage-free volatility surface.

Portfolio JSON example (replace the timestamp and values with an actual snapshot):

```json
{
  "asof": "2026-09-08T09:31:45+05:30",
  "verified": true,
  "equity": 500000,
  "available_cash": 300000,
  "peak_equity": 500000,
  "day_start_equity": 500000,
  "positions": [
    {
      "underlying": "^NSEI",
      "reserved_capital": 200000,
      "max_loss": 10000,
      "legs": [{"expiry": "2026-09-15", "strike": 23800, "option_type": "CE", "units": 65}]
    }
  ]
}
```

Existing leg units are signed. Portfolio equity is marked equity, and reserved
capital/max loss must include all existing positions, including broker-managed ones.
A snapshot older than 60 seconds, in the future, or unverified cannot confirm entries.
Absent portfolio information gives estimates only. Duplicate existing contract units
are aggregated before sizing and block additional allocation; no offsetting margin
benefit is assumed. Limits use existing RiskManager capital, whole-trade risk,
portfolio risk, margin utilization, position count, daily-loss and drawdown checks.
Paired structures receive equal lots/units and one payoff-based loss budget. Both
entry and estimated exit fees are reserved. Live buys use ask, shorts bid; spread,
quote age and both entry/exit displayed depth limit quantities. Uncovered shorts are
disabled in this report. Displayed depth does not guarantee a multi-leg fill. EOD
sizing uses archived lot sizes, not a guess from current configured defaults.

## Verified provider capabilities

The repository previously used yfinance for daily spot/VIX, NSE bhavcopy for EOD
options, and local CSV intraday replay. Bhavcopy does **not** supply synchronized
intraday quotes, spread/depth, or live IV. yfinance is an unofficial wrapper and is
not the execution quote provider.

Upstox was chosen for the user's free-data preference. Its official
[API overview](https://upstox.com/developer/api-documentation/open-api/) advertises
free API access. Account authentication and account-specific access still need to
be configured; no subscription or paid data was purchased. The read-only
`UpstoxProvider` implements documented
[one-minute intraday candles](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/)
and [full quotes with depth and timestamps](https://upstox.com/developer/api-documentation/get-full-market-quote/).
It shifts candle-start timestamps to completed interval ends, preserves vendor quote
timestamps, and never treats HTTP arrival time as quote freshness. API failures leave
setups unconfirmed. This REST adapter has not been authenticated/live-tested here.

Upstox also documents [expiry-specific option chains with IV/Greeks](https://upstox.com/developer/api-documentation/get-pc-option-chain/),
but that chain response alone does not establish the quote freshness required here.
The adapter uses full quotes and calculates midpoint IV locally. It does not claim
historical depth is supplied by historical candle endpoints. Alternative verified
Kite capabilities include [instruments/full quotes](https://kite.trade/docs/connect/v3/market-quotes/)
and [historical candles](https://kite.trade/docs/connect/v3/historical/); no Kite
adapter or subscription is required by this change.

Set `UPSTOX_ACCESS_TOKEN` securely in the local environment. Do not paste a token into
chat or save it in reports. Pass `--provider upstox --instruments data/upstox_instruments.json`
alongside the watchlist, current portfolio and contract master. For live Upstox omit
`--entry-at`: the decision timestamp is captured after the read-only data batch is
received. Replay accepts an explicit historical decision timestamp. The instrument mapping
contains `underlyings` (project ticker to official instrument key) and `contracts`
(rows of `underlying, instrument_key, expiry, strike, option_type`). Obtain these keys
and lot sizes from the provider's listed instrument master; do not construct tokens.
The adapter exposes no order, portfolio-mutation, or message methods. Historical
confirmation uses `ReplayProvider`, not current REST data relabeled as history.

## Chronological quality evaluation

```powershell
.venv-options\Scripts\python.exe main.py evaluate-signals --ticker '^NSEI' --bars data/nifty_minutes.csv --quotes data/option_quotes.csv --contract-master data/contracts.csv --train-sessions 60 --test-sessions 20 --holdout-sessions 20 --report-journal reports/daily-signals --output reports/quality-evaluation
```

This evaluates the existing quote-driven ORB/VWAP execution engine on archived
observations. Within each expanding training prefix it selects among ORB/VWAP and
expiry-day enabled/disabled policies by net expectancy, then runs the next unseen
window. The last holdout sessions are excluded from every development selection;
final selection uses only preceding data. Training with no trades selects no policy.
Data/execution failures propagate instead of producing flattering partial statistics.

Outputs include net expectancy in INR per complete trade, trade count, win rate,
closed-trade drawdown, and breakdowns by strategy, underlying, actual entry expiry
distance, prior-session market conditions, and expiry-day policy. Fold/holdout metrics
also contain observed intraday equity drawdown. Each fold starts with fresh capital;
breakdown drawdowns are realized subgroup P&L, not standalone hedge portfolios.
Conditions use the previous session's return (up >0.5%, down <-0.5%, otherwise flat),
never the closing condition of the entry day. Reusing a holdout after tuning consumes it.

Report coverage uses distinct generated-report dates from the separate JSON journal;
actionable frequency uses distinct confirmed-report dates. The denominator is observed
bar sessions, so it does not prove coverage of missing market-data sessions; provide a
complete session archive. With no journal both counts are zero. Evaluation does not
claim that an executed backtest trade proves a daily report was generated.

The evaluation smoke data and regression tests are explicitly synthetic. Real EOD
validation establishes data availability only. No synchronized historical quotes or
comparable IV history are bundled, and no improved accuracy, expectancy or win rate
is claimed. The existing daily-strategy walk-forward research remains separate from
this intraday evaluation and its daily ORB/VWAP proxies are not live confirmations.

Storage behavior is unchanged: 14-day `data/signal-bhavcopy/`, one option-cache key per
IST date, seven-day diagnostic retention, and untouched `data/bhavcopy/` historical
archive. The new report command explicitly selects the short signal cache even when
no workflow environment variable is present.
