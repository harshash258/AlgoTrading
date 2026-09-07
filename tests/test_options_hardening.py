from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
import pytest
from config import settings
from algo_trading.core.backtester import Backtester, Trade
from algo_trading.core.contracts import ContractMaster, ContractSpec
from algo_trading.core.pricing import bs_price, implied_volatility
from algo_trading.core.risk_manager import RiskManager
from algo_trading.core.structures import expiry_risk, validate_structure
from algo_trading.data.ingestion import ChainLookup
from algo_trading.reporting.metrics import compute_metrics
from algo_trading.strategies.base import Signal
from algo_trading.notifications import telegram
from tests.test_execution_realism import OneShotStrategy, _market_data

D = date(2026, 9, 4)
E = date(2026, 9, 29)


def leg(k, direction, kind="CE", premium=20):
    return Trade(str(k), "^NSEI", kind, direction, k, E, D,
                 entry_premium=premium, lot_size=65, entry_cost=2)


def chain(rows=None):
    rows = rows or [dict(date=d, expiry=E, strike=22000, opt_type="CE", open=20, close=25,
                        oi=5000, volume=500) for d in (D, date(2026, 9, 7), date(2026, 9, 8))]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["expiry"] = pd.to_datetime(df["expiry"])
    return ChainLookup(df)


def engine(signals=None, lookup=None, **kwargs):
    signals = signals or [Signal(D, "^NSEI", "long", "CE", strike=22000, expiry=E)]
    return Backtester(OneShotStrategy(signals), _market_data(), D, date(2026, 9, 8),
                      chain_lookups={"^NSEI": lookup or chain()}, **kwargs)


def test_exact_monthly_contract_is_not_replaced_by_near_expiry():
    rows = []
    for d in (D, date(2026, 9, 7), date(2026, 9, 8)):
        for exp, px in ((date(2026, 9, 15), 999), (E, 20)):
            rows.append(dict(date=d, expiry=exp, strike=22000, opt_type="CE", open=px, close=px))
    bt = engine(lookup=chain(rows))
    trades = bt.run()
    assert trades[0].expiry == E
    assert trades[0].entry_premium < 30
    assert trades[0].exit_premium < 30


def test_no_close_fallback_for_missing_open():
    lookup = chain([dict(date=D, expiry=E, strike=22000, opt_type="CE", open=0, close=999)])
    assert lookup.premium_with_meta(D, E, 22000, "CE", "open")[0] is None


def test_spread_max_loss_and_common_quantity():
    legs = [leg(22000, "long", premium=100), leg(22200, "short", premium=40)]
    validate_structure(legs, "bull_call_spread")
    loss, debit = expiry_risk(legs)
    assert loss == debit == 60 * 65 + 4
    credit = [leg(22000, "short", premium=100), leg(22200, "long", premium=40)]
    assert expiry_risk(credit)[0] == (200 - 60) * 65 + 4
    sigs = [Signal(D, "^NSEI", l.direction, l.option_type, strike=l.strike, expiry=E,
                   group_id="spread", structure_type="bull_call_spread") for l in legs]
    rows = [dict(date=d, expiry=E, strike=l.strike, opt_type="CE", open=l.entry_premium,
                 close=l.entry_premium) for d in (D, date(2026, 9, 7), date(2026, 9, 8)) for l in legs]
    bt = engine(sigs, chain(rows))
    bt.run()
    assert len(bt.closed_positions) == 1
    assert len({t.lots for t in bt.closed_trades}) == 1
    assert bt.closed_positions[0].max_loss <= 500000 * 0.02


def test_no_forced_lot_or_reuse_of_reserved_cash():
    rm = RiskManager(1000)
    assert rm.position_size(100, 65) == 0
    assert rm.structure_size(100, 2000) == 0
    rm.register_open("a", 990, 19)
    assert rm.structure_size(10, 20) == 0


def test_equity_reconciles_forced_exit_and_open_losses():
    rows = [dict(date=d, expiry=E, strike=22000, opt_type="CE", open=100, close=px)
            for d, px in ((D, 100), (date(2026, 9, 7), 90), (date(2026, 9, 8), 80))]
    bt = engine(lookup=chain(rows))
    trades = bt.run()
    eq = bt.get_equity_curve()
    assert eq.iloc[1].capital < 500000
    assert eq.iloc[1].unrealized_pnl < 0
    assert eq.iloc[-1].capital == pytest.approx(500000 + sum(t.net_pnl for t in trades))
    assert eq.iloc[-1].reserved_capital == 0


def test_strict_missing_hedge_rejects_whole_structure():
    sigs = [Signal(D, "^NSEI", side, "CE", strike=k, expiry=E, group_id="g",
                   structure_type="bull_call_spread") for side, k in (("long", 22000), ("short", 22200))]
    master = ContractMaster([ContractSpec("^NSEI", E, 65, D, E)])
    bt = engine(sigs, pricing_mode="strict", contract_master=master)
    assert bt.run() == []
    assert "Missing exact" in bt.rejections[0]["reason"]


def test_next_open_uses_previous_vix():
    bt = Backtester(
        OneShotStrategy([Signal(D, "^NSEI", "long", "CE", expiry=E)]),
        _market_data(), D, date(2026, 9, 8), starting_capital=5_000_000, chain_lookups={})
    bt.data["^NSEI"].loc[pd.Timestamp("2026-09-07"), "VIX"] = 80.0
    trades = bt.run()
    assert trades[0].entry_iv == pytest.approx(15.0)


def test_iv_inversion_round_trip():
    px = bs_price(22000, 22500, 0.1, 0.065, 0.24, "CE")
    assert implied_volatility(px, 22000, 22500, 0.1, 0.065, "CE") == pytest.approx(0.24)
    assert implied_volatility(50000, 22000, 22500, 0.1, 0.065, "CE") is None


def test_metrics_count_complete_positions():
    legs = [leg(22000, "long"), leg(22200, "short")]
    for l, pnl in zip(legs, (100, -20)):
        l.group_id = "spread"
        l.net_pnl = pnl
        l.pnl_pct = pnl
    metrics = compute_metrics(legs, pd.DataFrame({"capital": [1000, 1080]},
                              index=pd.to_datetime(["2026-09-04", "2026-09-07"])), 1000)
    assert metrics["total_trades"] == 1
    assert metrics["total_legs"] == 2
    assert metrics["win_rate_pct"] == 100


def test_telegram_rejects_entire_spread():
    sigs = [dict(strategy_label="spread", group_id="g", structure_type="bull_call_spread",
                 direction=side, option_type="CE", strike=k, expiry=E, meta={})
            for side, k in (("long", 22000), ("short", 22200))]
    collected = {"^NSEI": dict(name="Nifty", spot=22000, atm=22000, sigs=sigs)}
    with patch.object(telegram, "_validate_contract_signal", side_effect=["", "missing hedge"]):
        filtered, rejected = telegram._validate_collected_signals({}, collected, D)
    assert filtered == {}
    assert "entire structure" in rejected[0]


def test_walk_forward_selects_only_on_training(monkeypatch, tmp_path):
    from algo_trading.optimization import walk_forward as wf
    import config
    monkeypatch.setattr(config, "REPORTS_DIR", str(tmp_path))
    candidates = dict(fast_ma=[2], slow_ma=[3], rsi_oversold=[30], rsi_overbought=[70],
                      stop_loss_pct=[40], target_pct=[100])
    training_ends = []
    def search(params, data, start, end, *args, **kwargs):
        assert data["^NSEI"].index.max().date() <= end
        training_ends.append(end)
        return pd.DataFrame([{**params[0], "profit_factor": 1.1, "rank": 1}])
    monkeypatch.setattr(wf, "search_parameters", search)
    monkeypatch.setattr(wf, "_run_period", lambda *a, **k: {"total_trades": 2})
    data = {"^NSEI": pd.DataFrame({"Close": 1}, index=pd.date_range("2018-01-01", "2025-12-31"))}
    result = wf.run_walk_forward(start=date(2018, 1, 1), end=date(2025, 12, 31),
                                 market_data=data, grid=candidates)
    assert result.iloc[-1].fold_num == "holdout"
    assert max(training_ends) < date(2025, 1, 1)
    with pytest.raises(ValueError, match="predeclared"):
        wf.run_walk_forward(top_params=[{}])


def test_missing_exit_quote_does_not_discard_open_position():
    rows = [dict(date=d, expiry=E, strike=22000, opt_type="CE", open=20, close=25,
                 oi=5000, volume=500) for d in (D, date(2026, 9, 7))]
    bt = engine(lookup=chain(rows), pricing_mode="strict",
                contract_master=ContractMaster([ContractSpec("^NSEI", E, 65, D, E)]))
    with pytest.raises(ValueError, match="missing quote"):
        bt.run()
    assert len(bt.open_positions) == 1
    assert len(bt.open_trades) == 1
    assert not bt.closed_trades


def test_historical_lot_size_applies_to_contract():
    bt = engine(contract_master=ContractMaster([ContractSpec("^NSEI", E, 25, D, E)]))
    assert bt.run()[0].lot_size == 25


def test_effective_dated_fees(monkeypatch, tmp_path):
    from algo_trading.core.fees import RATE_FIELDS
    from algo_trading.core.pricing import calculate_transaction_cost
    row = {k: getattr(settings, k) for k in RATE_FIELDS}
    rows = [{**row, "effective_from": "2020-01-01", "effective_to": "2020-12-31", "STT_SELL_PCT": 0.1},
            {**row, "effective_from": "2021-01-01", "effective_to": "2021-12-31", "STT_SELL_PCT": 0.2}]
    path = tmp_path / "fees.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    monkeypatch.setattr(settings, "FEE_SCHEDULE_PATH", str(path))
    old = calculate_transaction_cost(100, 100, 1, "sell", date(2020, 6, 1))
    new = calculate_transaction_cost(100, 100, 1, "sell", date(2021, 6, 1))
    assert new - old == pytest.approx(10)
    with pytest.raises(ValueError, match="Missing"):
        calculate_transaction_cost(100, 100, 1, "sell", D)


def test_full_execution_risk_parameters_change_exit_date():
    rows = [dict(date=d, expiry=E, strike=22000, opt_type="CE", open=20, close=px)
            for d, px in ((D, 20), (date(2026, 9, 7), 35), (date(2026, 9, 8), 80))]
    quick = engine(lookup=chain(rows), risk_overrides={"BUY_TARGET_PCT": 50})
    slow = engine(lookup=chain(rows), risk_overrides={"BUY_TARGET_PCT": 200})
    assert quick.run()[0].exit_date < slow.run()[0].exit_date


def test_portfolio_drawdown_uses_open_losses():
    rm = RiskManager(1000)
    rm.mark_to_market(-200)
    assert rm.drawdown_pct == pytest.approx(20)
    assert rm.capital == 1000
    assert not rm.can_open_trade()[0]


def test_report_renders_with_position_metrics(tmp_path):
    from algo_trading.reporting.html_generator import generate_html_report
    bt = engine()
    trades = bt.run()
    path = generate_html_report(trades, bt.get_equity_curve(), "smoke", {}, ["^NSEI"], D, E,
                                output_dir=str(tmp_path))
    from pathlib import Path
    html = Path(path).read_text(encoding="utf-8")
    assert "Total Positions" in html
    assert "Daily Sharpe" in html
    assert list(tmp_path.glob("*_diagnostics.json"))


@pytest.mark.parametrize("name", ["bull_call_spread", "bear_put_spread", "bull_put_spread", "bear_call_spread"])
def test_spread_strategy_emits_valid_orientation(name):
    from algo_trading.strategies import get_strategy
    strategy = get_strategy(name)
    direction = "CE" if strategy.bullish else "PE"
    with patch.object(strategy.signal_strategy, "generate_signals", return_value=[Signal(D, "^NSEI", "long", direction, expiry=E)]):
        df = _market_data()["^NSEI"]
        sigs = strategy.generate_signals(df, df.VIX, D)
    legs = [leg(s.strike, s.direction, s.option_type) for s in sigs]
    validate_structure(legs, name)
    assert len(sigs) == 2
    assert sigs[0].group_id == sigs[1].group_id


def test_rerunning_engine_resets_strategy_state():
    bt = engine()
    first = bt.run()
    second = bt.run()
    assert len(first) == len(second) == 1
    assert first[0].net_pnl == second[0].net_pnl


def test_signal_exit_waits_for_next_open():
    sigs = [Signal(D, "^NSEI", "long", "CE", strike=22000, expiry=E),
            Signal(date(2026, 9, 7), "^NSEI", "long", "CE", signal_type="exit")]
    bt = engine(signals=sigs)
    trades = bt.run()
    assert trades[0].exit_date == date(2026, 9, 8)
    assert trades[0].exit_pricing_source == "bhavcopy_open"


def test_strict_liquidity_uses_previous_day():
    rows = [dict(date=d, expiry=E, strike=22000, opt_type="CE", open=20, close=25,
                 oi=5000, volume=0 if d == D else 99999)
            for d in (D, date(2026, 9, 7), date(2026, 9, 8))]
    bt = engine(lookup=chain(rows), pricing_mode="strict",
                contract_master=ContractMaster([ContractSpec("^NSEI", E, 65, D, E)]))
    assert bt.run() == []
    assert "insufficient volume" in bt.rejections[0]["reason"]


def test_inversion_preserves_long_side_and_rejects_spreads():
    from algo_trading.strategies.inverse_strategy import _flip_signal
    flipped = _flip_signal(Signal(D, "^NSEI", "long", "CE"))
    assert (flipped.option_type, flipped.direction) == ("PE", "long")
    with pytest.raises(ValueError, match="dedicated spread"):
        _flip_signal(Signal(D, "^NSEI", "long", "CE", structure_type="iron_condor"))


def test_optimizer_runs_every_full_configuration(monkeypatch):
    from algo_trading.optimization import grid_search as grid
    calls = []
    def run(params, *args, **kwargs):
        calls.append(params["stop_loss_pct"])
        bt = engine()
        return bt.run(), bt.get_equity_curve()
    monkeypatch.setattr(grid, "_run_signal_backtest", run)
    base = dict(fast_ma=2, slow_ma=3, rsi_oversold=30, rsi_overbought=70, target_pct=100)
    result = grid.search_parameters([{**base, "stop_loss_pct": x} for x in (10, 50)], _market_data(), D, E, min_trades=1)
    assert calls == [10, 50]
    assert len(result) == 2


def test_surface_contains_contract_iv():
    px = bs_price(22000, 22000, (E-D).days / 365, 0.065, 0.2, "CE")
    surface = chain([dict(date=D, expiry=E, strike=22000, opt_type="CE", open=px, close=px)]).volatility_surface(D, 22000)
    assert surface.iloc[0].iv == pytest.approx(0.2)
    assert surface.iloc[0].dte == 25


def test_overnight_loss_blocks_next_open_entry():
    dates = [D, date(2026, 9, 7), date(2026, 9, 8)]
    rows = [dict(date=d, expiry=E, strike=k, opt_type="CE", open=px, close=100)
            for d, px in zip(dates, (100, 100, 1)) for k in (22000, 22100)]
    signals = [Signal(D, "^NSEI", "long", "CE", strike=22000, expiry=E),
               Signal(date(2026, 9, 7), "^NSEI", "long", "CE", strike=22100, expiry=E)]
    with patch.object(settings, "MAX_DAILY_LOSS_PCT", 0.5):
        bt = engine(signals=signals, lookup=chain(rows))
        bt.run()
    assert len(bt.closed_trades) == 1
    assert any("Daily loss" in r["reason"] for r in bt.rejections)
