from datetime import date
import json
import pandas as pd
import pytest
from algo_trading.core.contracts import ContractMaster, ContractSpec
from algo_trading.core.volatility import contract_iv_context, expiry_days
from algo_trading.reporting.daily_signals import build_daily_report, QualityPolicy, save_daily_report
from algo_trading.reporting.signal_evaluation import coverage, chronological_evaluation, summary
from algo_trading.data.providers import ReplayProvider, UpstoxProvider

D = date(2026, 9, 7)
E = date(2026, 9, 15)
T = pd.Timestamp('2026-09-08T09:32:00+05:30')


def master(expiry=E):
    return ContractMaster([ContractSpec('^NSEI', expiry, 25, D, expiry)])


def candidate(label='ORB', paired=False, expiry=E):
    legs = [dict(strategy_label=label, direction='long', option_type=k, expiry=expiry,
                 strike=100, structure_type='straddle' if paired else 'single', group_id='pair' if paired else None,
                 meta={'validated_premium': 2}) for k in (('CE', 'PE') if paired else ('CE',))]
    return {'^NSEI': {'spot': 100, 'sigs': legs}}


def portfolio(**kwargs):
    return dict(equity=100000, available_cash=100000, verified=True, asof=T.isoformat(), **kwargs)


class Provider:
    name = 'synthetic_fixture'
    def __init__(self, quote_ts=T, bid=1.99, ask=2, size=10000, expiry=E, volume=100):
        self.q = pd.DataFrame([dict(underlying='^NSEI', expiry=expiry, strike=100, option_type=k,
            bid=bid, ask=ask, bid_size=size, ask_size=size) for k in ('CE','PE')], index=[quote_ts]*2)
        index = pd.date_range('2026-09-08T09:16:00+05:30', periods=17, freq='min')
        self.b = pd.DataFrame(dict(Open=100.,High=101.,Low=99.,Close=100.,Volume=volume),index=index)
        self.b.loc[index[-2]:, ['High','Close']] = 102.
    def candles(self, ticker, asof): return self.b.copy()
    def quotes(self, ticker, asof): return self.q.copy()


def run(provider=None, collected=None, **kwargs):
    return build_daily_report(collected or candidate(), D, T, master=kwargs.pop('master',master()),
                              provider=provider, portfolio=kwargs.pop('portfolio',portfolio()), **kwargs)


def test_eod_is_estimate_never_confirmation(tmp_path):
    report = run()
    assert not report['confirmed_entries']
    setup = report['awaiting_triggers'][0]
    assert setup['lots'] == 0 and setup['estimated_lots'] > 0
    assert setup['legs'][0]['units'] == 0
    save_daily_report(report,tmp_path)
    assert 'Setups awaiting triggers' in (tmp_path/'report.md').read_text(encoding='utf-8')


def test_actual_orb_and_fresh_quotes_confirm():
    report = run(Provider())
    setup = report['confirmed_entries'][0]
    assert setup['lots'] > 0
    assert setup['legs'][0]['units'] == setup['lots'] * 25


@pytest.mark.parametrize('quote_ts', [T-pd.Timedelta(seconds=31),T+pd.Timedelta(seconds=1)])
def test_stale_future_quotes_block(quote_ts):
    report = run(Provider(quote_ts=quote_ts))
    assert not report['confirmed_entries']
    assert any('fresh exact-contract' in r for r in report['no_trade_reasons'])


@pytest.mark.parametrize('bid,ask,size',[(1,2,10000),(3,2,10000),(1.99,2,0),(float('nan'),2,1000)])
def test_spread_crossed_nan_and_depth_block(bid,ask,size):
    assert not run(Provider(bid=bid,ask=ask,size=size))['confirmed_entries']


def test_missing_opening_bar_and_index_volume_block():
    p=Provider();p.b=p.b.iloc[1:]
    assert not run(p)['confirmed_entries']
    assert not run(Provider(volume=0),candidate('Session VWAP'))['confirmed_entries']


def test_stale_portfolio_blocks_confirmation():
    state=portfolio();state['asof']=(T-pd.Timedelta(seconds=61)).isoformat()
    assert not run(Provider(),portfolio=state)['confirmed_entries']


def test_duplicate_existing_contract_blocks_allocation():
    state=portfolio(positions=[dict(underlying='^NSEI',reserved_capital=100,max_loss=100,
        legs=[dict(expiry=str(E),strike=100,option_type='CE',units=25)])])
    report=run(Provider(),portfolio=state)
    assert not report['confirmed_entries']
    assert any('duplicate' in r for r in report['no_trade_reasons'])


def test_available_cash_limits_and_one_primary():
    state=portfolio();state['available_cash']=0
    assert not run(Provider(),portfolio=state)['confirmed_entries']
    c=candidate();c['^NSEI']['sigs']+=candidate('MA Crossover')['^NSEI']['sigs']
    report=run(Provider(),c)
    assert len(report['confirmed_entries']) == 1
    assert len(report['alternatives']) == 1


def test_equal_pair_size_one_budget():
    report=run(Provider(),candidate('Pair context',paired=True))
    setup=report['confirmed_entries'][0]
    assert setup['legs'][0]['units']==setup['legs'][1]['units']
    assert setup['whole_trade_budget'] <= 100000 * .02
    assert setup['whole_trade_budget']==setup['lots']*setup['max_loss_per_lot']


def test_expiry_day_policy_at_entry():
    expiry=T.date()
    report=run(Provider(expiry=expiry),candidate(expiry=expiry),master=master(expiry))
    assert not report['confirmed_entries']
    allowed=run(Provider(expiry=expiry),candidate(expiry=expiry),master=master(expiry),policy=QualityPolicy(allow_expiry_day=True))
    assert allowed['confirmed_entries'][0]['expiry_day']
    assert 0 < expiry_days(expiry,T) < 1
    late=run(Provider(expiry=expiry),candidate(expiry=expiry),master=master(expiry),policy=QualityPolicy(allow_expiry_day=True,expiry_day_cutoff='09:30'))
    assert not late['confirmed_entries']


def test_iv_comparison_excludes_future_other_underlyings_and_tenors():
    rows=[dict(timestamp=f'2026-08-{i:02}T15:30:00+05:30',underlying='^NSEI',option_type='CE',dte=5,moneyness=1,iv=.1) for i in range(1,21)]
    rows += [dict(rows[0],timestamp=T.isoformat(),iv=4),dict(rows[0],underlying='^NSEBANK',iv=4),dict(rows[0],dte=25,iv=4)]
    context=contract_iv_context(.2,pd.DataFrame(rows),'^NSEI','CE',5,1,T)
    assert context['percentile']==100 and context['samples']==20
    assert contract_iv_context(.2,pd.DataFrame(rows[:2]),'^NSEI','CE',5,1,T)['percentile'] is None


def test_volatility_setup_requires_contract_history():
    report=run(Provider(),candidate('Long Straddle (Low IV)',paired=True))
    assert not report['confirmed_entries']
    assert any('IV history' in r for r in report['no_trade_reasons'])


def test_report_coverage_not_trade_frequency():
    metrics=coverage([D,T.date()],[dict(asof=str(D),report_generated=True,actionable=False),
                              dict(asof=str(T.date()),report_generated=True,actionable=True)])
    assert metrics['report_coverage']==1 and metrics['actionable_frequency']==.5
    assert summary([{'net_pnl':-10},{'net_pnl':20},{'net_pnl':-5}])['max_drawdown_rupees']==10


def test_replay_cannot_see_future(tmp_path):
    p=Provider()
    p.b.rename_axis('timestamp').to_csv(tmp_path/'bars.csv')
    p.q.rename_axis('timestamp').to_csv(tmp_path/'quotes.csv')
    replay=ReplayProvider({'^NSEI':tmp_path/'bars.csv'},tmp_path/'quotes.csv')
    assert replay.quotes('^NSEI',T-pd.Timedelta(seconds=1)).empty
    assert replay.candles('^NSEI',T-pd.Timedelta(minutes=1)).index.max()<T


def test_upstox_uses_observation_timestamp_and_completed_intervals():
    class Session:
        def get(self,url,**kwargs):
            assert 'orders' not in url
            if 'historical-candle' in url:
                data={'candles':[['2026-09-08T09:31:00+05:30',100,101,99,100,10,0],['2026-09-08T09:32:00+05:30',100,101,99,100,10,0]]}
            else:
                data={'a':dict(instrument_token='NSE_FO|1',timestamp='2026-09-08T09:31:00+05:30',depth=dict(buy=[dict(price=1,quantity=25)],sell=[dict(price=2,quantity=25)]))}
            class Response:
                status_code=200
                def json(self):return dict(status='success',data=data)
            return Response()
    provider=UpstoxProvider('fixture',dict(underlyings={'^NSEI':'NSE_INDEX|Nifty 50'},contracts=[dict(underlying='^NSEI',instrument_key='NSE_FO|1',expiry=str(E),strike=100,option_type='CE')]),Session())
    assert list(provider.candles('^NSEI',T).index)==[T]
    assert provider.quotes('^NSEI',T).index[0] == T-pd.Timedelta(minutes=1)


def test_chronological_holdout_does_not_leak(monkeypatch):
    import algo_trading.reporting.signal_evaluation as ev
    seen=[]
    class Engine:
        def __init__(self,b,q,*args,**kwargs):self.b=b;self.equity_curve=[];seen.append(set(b.index.date))
        def run(self):return []
    monkeypatch.setattr(ev,'IntradayBacktester',Engine)
    index=pd.date_range('2026-08-03T09:31:00+05:30',periods=8,freq='B')
    bars=pd.DataFrame({'Close':100},index=index)
    report=chronological_evaluation(bars,pd.DataFrame(index=index),'^NSEI',master(),3,2,2)
    holdout=set(index[-2:].date)
    assert all(not (days & holdout) for days in seen) # no trades => no policy selected
    assert report['holdout']['metrics']['trade_count']==0
    assert report['holdout']['selected'] is None

def test_missing_spec_keeps_unsized_primary_watchlist():
    report=run(master=ContractMaster())
    assert len(report['awaiting_triggers'])==1
    assert report['awaiting_triggers'][0]['estimated_lots']==0
    assert not report['confirmed_entries']
    assert not any('Incomplete' in r for r in report['no_trade_reasons'])


def test_quality_collector_filters_expiry_at_intended_entry(monkeypatch):
    from algo_trading.notifications import telegram
    from tests.test_live_expiries import lookup, ListedExpiryStrategy
    from tests.test_execution_realism import _market_data
    monkeypatch.setattr(telegram,'_get_chain_lookup',lambda _:lookup([T.date(),E]))
    monkeypatch.setattr(telegram,'_all_strategies',lambda:[('test',ListedExpiryStrategy())])
    result,_=telegram._collect_signals(_market_data(dates=[str(D)]),D,quality_mode=True,expiry_filter=lambda e:e>T.date())
    assert result['^NSEI']['sigs'][0]['expiry']==E


def test_quality_collector_never_calls_daily_orb_vwap(monkeypatch):
    from algo_trading.notifications import telegram
    class Proxy:
        def generate_signals(self,*args):raise AssertionError('daily proxy used')
    monkeypatch.setattr(telegram,'_all_strategies',lambda:[(name,Proxy()) for name in ('Opening Range Breakout','VWAP Reversion','VWAP Breakout')])
    result,reasons=telegram._collect_signals({},D,quality_mode=True)
    assert not result and not reasons


def test_contract_iv_history_counts_sessions_not_duplicate_ticks():
    h=pd.DataFrame([dict(timestamp='2026-09-01T10:00:00+05:30',underlying='^NSEI',option_type='CE',dte=5,moneyness=1,iv=.1)]*30)
    result=contract_iv_context(.2,h,'^NSEI','CE',5,1,T)
    assert result['samples']==1 and result['percentile'] is None


def test_nan_policy_and_portfolio_fail_closed():
    with pytest.raises(ValueError):QualityPolicy(max_spread_pct=float('nan'))
    with pytest.raises(ValueError):run(portfolio={'equity':float('nan')})


def test_daily_portfolio_drawdown_blocks_entries():
    state=portfolio();state['peak_equity']=200000
    assert not run(Provider(),portfolio=state)['confirmed_entries']

def test_holdout_price_changes_cannot_change_training_selection(monkeypatch):
    from types import SimpleNamespace
    import algo_trading.reporting.signal_evaluation as ev
    class Engine:
        def __init__(self,b,q,ticker,master,strategy,capital,**kwargs):
            self.b,self.strategy=b,strategy
            self.equity_curve=[dict(capital=capital)]
        def run(self):
            pnl=float(self.b.Close.sum())*(1 if self.strategy=='orb' else -1)
            return [SimpleNamespace(entry_date=self.b.index[0],exit_date=self.b.index[-1],expiry=E,net_pnl=pnl)]
    monkeypatch.setattr(ev,'IntradayBacktester',Engine)
    index=pd.date_range('2026-08-03T09:31:00+05:30',periods=8,freq='B')
    bars=pd.DataFrame({'Close':100.},index=index)
    before=chronological_evaluation(bars,pd.DataFrame(index=index),'^NSEI',master(),3,2,2)
    bars.iloc[-2:]=999
    after=chronological_evaluation(bars,pd.DataFrame(index=index),'^NSEI',master(),3,2,2)
    assert before['folds']==after['folds']
    assert before['holdout']['selected']==after['holdout']['selected']
    assert before['holdout']['metrics']!=after['holdout']['metrics']


def test_captured_provider_filters_future_observations():
    from algo_trading.data.providers import CapturedProvider
    p=Provider()
    snap=CapturedProvider('fixture',{'^NSEI':p.b},{'^NSEI':p.q},T)
    assert snap.quotes('^NSEI',T-pd.Timedelta(seconds=1)).empty
    assert snap.candles('^NSEI',T-pd.Timedelta(minutes=1)).index.max()<T
