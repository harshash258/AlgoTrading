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
