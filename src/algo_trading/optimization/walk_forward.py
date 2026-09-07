"""Nested chronological selection with a reserved final holdout.

Each fold searches a predeclared grid using training data only. Indicators may
use earlier warmup history, but orders are restricted to the evaluation window.
"""
from datetime import date, timedelta
from pathlib import Path
from dateutil.relativedelta import relativedelta
import pandas as pd
import config
from algo_trading.data.ingestion import get_combined_dataset
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.optimization.grid_search import (
    FULL_GRID, QUICK_GRID, _build_combinations, search_parameters, _run_signal_backtest,
)


def generate_folds(full_start, full_end, train_years=3, test_years=1, step_years=1):
    if min(train_years, test_years, step_years) <= 0 or step_years < test_years:
        raise ValueError("Windows must be positive and test windows must not overlap")
    folds = []
    while True:
        train_end = full_start + relativedelta(years=train_years) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + relativedelta(years=test_years) - timedelta(days=1)
        if test_end > full_end:
            break
        folds.append(dict(fold_num=len(folds) + 1, train_start=full_start, train_end=train_end,
                          test_start=test_start, test_end=test_end))
        full_start += relativedelta(years=step_years)
    return folds


def _run_period(params, market_data, chain_lookup, start, end, **engine_options):
    history = {k: v.loc[v.index.date <= end].copy() for k, v in market_data.items()}
    trades, equity = _run_signal_backtest(params, history, chain_lookup, start, end, **engine_options)
    return compute_metrics(trades, equity, config.STARTING_CAPITAL)


def run_walk_forward(top_params=None, train_years=3, test_years=1, step_years=1,
                     start=None, end=None, tickers=None, chain_lookup=None, market_data=None,
                     grid=None, quick=False, min_trades=10, top_n=1, holdout_years=1,
                     **engine_options):
    if top_params is not None:
        raise ValueError("Pass a predeclared grid, not parameters selected on full history")
    if holdout_years < 1 or top_n < 1:
        raise ValueError("Reserve at least one holdout year and select at least one candidate")
    start, end = start or config.BACKTEST_START, end or config.BACKTEST_END
    holdout_start = end - relativedelta(years=holdout_years) + timedelta(days=1)
    development_end = holdout_start - timedelta(days=1)
    folds = generate_folds(start, development_end, train_years, test_years, step_years)
    if not folds:
        raise ValueError("Insufficient history for training, test and final holdout")
    if market_data is None:
        market_data = get_combined_dataset(start=start, end=end)
    market_data = {k: v for k, v in market_data.items() if k in (tickers or config.UNDERLYINGS)}
    candidates = _build_combinations(grid or (QUICK_GRID if quick else FULL_GRID))
    folds.append(dict(fold_num="holdout", train_start=holdout_start - relativedelta(years=train_years),
                      train_end=development_end, test_start=holdout_start, test_end=end))
    rows = []
    for fold in folds:
        print(f"Selecting within training window {fold['train_start']} to {fold['train_end']}", flush=True)
        training = {k: v.loc[v.index.date <= fold["train_end"]].copy() for k, v in market_data.items()}
        ranked = search_parameters(candidates, training, fold["train_start"], fold["train_end"],
                                   min_trades, chain_lookup, **engine_options)
        if ranked.empty:
            rows.append({**fold, "status": "no_eligible_training_candidate"})
            continue
        for chosen in ranked.head(top_n).to_dict("records"):
            params = {k: chosen[k] for k in candidates[0]}
            metrics = _run_period(params, market_data, chain_lookup,
                                  fold["test_start"], fold["test_end"], **engine_options)
            rows.append({**fold, **params, "selection_rank": chosen["rank"],
                         "train_pf": chosen["profit_factor"], "status": "evaluated",
                         **{f"test_{k}": v for k, v in metrics.items()}})
    result = pd.DataFrame(rows)
    Path(config.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    result.to_csv(Path(config.REPORTS_DIR) / f"walk_forward_results_{date.today()}.csv", index=False)
    return result
