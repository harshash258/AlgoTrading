from datetime import date

import pytest

from algo_trading.notifications import telegram


def signal(kind="CE", premium=100, paired=False):
    return dict(strategy_label="Straddle" if paired else "Bollinger", option_type=kind,
                is_short=False, direction="long", expiry=date(2026, 9, 8), strike=23800,
                structure_type="straddle" if paired else "single",
                meta=dict(validated_premium=premium, paired_legs=paired, pair_type="straddle"))


@pytest.fixture(autouse=True)
def sizing_config(monkeypatch):
    monkeypatch.setattr(telegram.config, "STARTING_CAPITAL", 500000)
    monkeypatch.setattr(telegram.config, "RISK_PER_TRADE_PCT", 2.0)
    monkeypatch.setattr(telegram.config, "LOT_SIZES", {"^NSEI": 65})


def test_single_sizes_units_from_premium_not_lot_size():
    text = telegram._format_size_estimate([signal()], "^NSEI")
    assert "Est lots: 1" in text
    assert "Quantity: 65 units" in text
    assert "Max loss: ₹6,500.00" in text


def test_pair_shares_one_budget_and_equal_quantities():
    group = dict(label="Straddle", ce=signal(premium=40, paired=True),
                 pe=signal("PE", premium=30, paired=True))
    text = telegram._format_paired_block(group, 23779, 11.2, 23800, "^NSEI")
    assert "Est lots: 2 per leg" in text
    assert "Quantity: 130 units per leg" in text
    assert "Max loss: ₹9,100.00" in text
    assert "whole trade" in text
    assert "× 2 legs" not in text


def test_zero_lots_when_combined_premium_exceeds_budget():
    text = telegram._format_size_estimate([signal(paired=True), signal("PE", paired=True)], "^NSEI")
    assert "Est lots: 0" in text
    assert "SKIP" in text


@pytest.mark.parametrize("premium", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_premium_does_not_invent_quantity(premium):
    text = telegram._format_size_estimate([signal(premium=premium)], "^NSEI")
    assert "Quantity: unavailable" in text
    assert "Est lots" not in text


def test_short_or_incomplete_structure_not_sized_as_long():
    short = signal()
    short["is_short"] = True
    for sig in [short, signal(paired=True)]:
        assert "Quantity: unavailable" in telegram._format_size_estimate([sig], "^NSEI")


def test_overlap_notice_preserves_both_ideas():
    data = dict(name="NIFTY", spot=23779, vix=11.2, atm=23800,
                sigs=[signal(paired=True), signal("PE", paired=True), signal()])
    text = telegram._format_ticker_block("^NSEI", data)
    assert "Overlap: 23800 CE" in text
    assert "not a combined portfolio allocation" in text
    assert "BUY VOLATILITY" in text and "Bollinger" in text
    data["sigs"][-1]["expiry"] = date(2026, 9, 15)
    assert "Overlap:" not in telegram._format_ticker_block("^NSEI", data)
