from algo_trading.notifications.telegram import (
    _dedupe_vol_pairs,
    _format_ticker_block,
)


def _leg(label: str, option_type: str, strike: float, pair_type: str) -> dict:
    return {
        "strategy_label": label,
        "direction": "long",
        "option_type": option_type,
        "expiry": None,
        "strike": strike,
        "confidence": 0.82,
        "is_short": False,
        "meta": {
            "paired_legs": True,
            "pair_type": pair_type,
            "variant": pair_type,
            "trigger": "IV percentile 23.8% <= 30% (vol cheap)",
            "confidence": 0.82,
        },
    }


def test_dedupe_vol_pairs_prefers_straddle_over_strangle():
    straddle = {
        "label": "Long Straddle (Low IV)",
        "ce": _leg("Long Straddle (Low IV)", "CE", 24350, "straddle"),
        "pe": _leg("Long Straddle (Low IV)", "PE", 24350, "straddle"),
    }
    strangle = {
        "label": "Long Strangle (Low IV)",
        "ce": _leg("Long Strangle (Low IV)", "CE", 24650, "strangle"),
        "pe": _leg("Long Strangle (Low IV)", "PE", 24150, "strangle"),
    }

    assert _dedupe_vol_pairs([strangle, straddle]) == [straddle]


def test_ticker_block_formats_straddle_as_one_volatility_trade():
    ticker_data = {
        "name": "NIFTY 50",
        "spot": 24366,
        "vix": 11.3,
        "atm": 24350,
        "sigs": [
            _leg("Long Straddle (Low IV)", "CE", 24350, "straddle"),
            _leg("Long Straddle (Low IV)", "PE", 24350, "straddle"),
            _leg("Long Strangle (Low IV)", "CE", 24650, "strangle"),
            _leg("Long Strangle (Low IV)", "PE", 24150, "strangle"),
        ],
    }

    block = _format_ticker_block("^NSEI", ticker_data)

    assert "BUY VOLATILITY - STRADDLE" in block
    assert "Confidence: 82%" in block
    assert "CE + PE is intentional, not a conflict" in block
    assert "Long Strangle (Low IV)" not in block
    assert "BUY VOLATILITY - STRANGLE" not in block


def test_ticker_block_formats_standalone_signal_confidence():
    ticker_data = {
        "name": "NIFTY 50",
        "spot": 24366,
        "vix": 11.3,
        "atm": 24350,
        "sigs": [{
            "strategy_label": "MA Crossover",
            "direction": "long",
            "option_type": "CE",
            "expiry": None,
            "strike": 24350,
            "confidence": 0.67,
            "is_short": False,
            "meta": {"trigger": "MA crossover", "confidence": 0.67},
        }],
    }

    block = _format_ticker_block("^NSEI", ticker_data)

    assert "BUY CE" in block
    assert "Confidence: 67%" in block
