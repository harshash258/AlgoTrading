"""
telegram_notify.py — Send trading signals to a Telegram chat.

Runs ALL 13 strategies independently on today's data. Any strategy that
fires a signal contributes it to the message with its name clearly labelled.
Multiple signals on the same underlying are grouped. Conflicts (CE + PE on
the same ticker) are flagged so you can decide.

Usage (standalone):
    python src/telegram_notify.py

Environment variables required:
    TELEGRAM_BOT_TOKEN  : Bot token from @BotFather
    TELEGRAM_CHAT_ID    : Your chat ID (get from @userinfobot)
"""

import os
import sys
import logging
import math
import pandas as pd
from html import escape
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from algo_trading.data.ingestion import get_combined_dataset
from algo_trading.core.backtester import _get_chain_lookup
from algo_trading.data.signal_data import prepare_signal_data, india_today

# ── All strategies ────────────────────────────────────────────────
from algo_trading.strategies.trend_following import TrendFollowingStrategy
from algo_trading.strategies.rsi_strategy import RSIStrategy
from algo_trading.strategies.confluence_strategy import ConfluenceStrategy
from algo_trading.strategies.mean_reversion import MeanReversionStrategy
from algo_trading.strategies.bollinger_band_strategy import BollingerBandStrategy
from algo_trading.strategies.orb_strategy import ORBStrategy
from algo_trading.strategies.long_straddle import LongStraddleStrategy
from algo_trading.strategies.vwap_reversion import VWAPReversionStrategy
from algo_trading.strategies.gap_fade import GapFadeStrategy
from algo_trading.strategies.iron_condor import IronCondorStrategy

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def _html_text(value) -> str:
    """Escape dynamic text before embedding it in Telegram HTML messages."""
    return escape(str(value), quote=False)


# ─────────────────────────────────────────────────────────────────
# Strategy registry — every strategy the bot should run
# Each entry: (display_name, factory_fn)
# ─────────────────────────────────────────────────────────────────

def _all_strategies() -> list[tuple[str, object]]:
    """
    Return a list of (display_label, strategy_instance) for every strategy.
    Fresh instances are created each call so state never bleeds between runs.
    """
    return [
        ("MA Crossover", TrendFollowingStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            use_adx_filter=False,
            use_confirm=False,
            use_direction_filter=False,
            use_time_stop=False,
        )),
        ("MA Crossover (Full Filters)", TrendFollowingStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            use_adx_filter=True,
            use_confirm=True,
            use_direction_filter=True,
            use_time_stop=True,
        )),
        ("RSI Reversal", RSIStrategy(
            rsi_period=config.RSI_PERIOD,
            oversold=config.RSI_OVERSOLD,
            overbought=config.RSI_OVERBOUGHT,
            confirm_bars=config.RSI_CONFIRM_BARS,
            time_stop_days=config.RSI_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Confluence (MA + RSI)", ConfluenceStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            rsi_period=config.RSI_PERIOD,
            rsi_oversold=config.RSI_OVERSOLD,
            rsi_overbought=config.RSI_OVERBOUGHT,
            rsi_lookback=config.CONFLUENCE_RSI_LOOKBACK,
            rsi_entry_max=config.CONFLUENCE_RSI_ENTRY_MAX,
            rsi_entry_min=config.CONFLUENCE_RSI_ENTRY_MIN,
            use_trend_filter=config.CONFLUENCE_USE_TREND_FILTER,
            vix_min=config.BB_VIX_MIN,
            vix_max=config.BB_VIX_MAX,
            time_stop_days=config.CONFLUENCE_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Opening Range Breakout", ORBStrategy(
            breakout_pct=config.ORB_BREAKOUT_PCT,
            gap_max_pct=config.ORB_GAP_MAX_PCT,
            use_adx_filter=config.ORB_USE_ADX_FILTER,
            time_stop_days=config.ORB_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Short Strangle (High IV)", MeanReversionStrategy(
            iv_entry_pct=config.MR_IV_PERCENTILE_ENTRY,
            iv_exit_pct=config.MR_IV_PERCENTILE_EXIT,
            otm_delta=config.MR_OTM_DELTA,
            weekly=False,
        )),
        ("Bollinger Band Reversion", BollingerBandStrategy(
            bb_period=config.BB_PERIOD,
            bb_std=config.BB_STD_MULT,
            atr_period=config.BB_ATR_PERIOD,
            use_trend_filter=config.BB_USE_TREND_FILTER,
            vix_min=config.BB_VIX_MIN,
            vix_max=config.BB_VIX_MAX,
            confirm_bars=config.BB_CONFIRM_BARS,
            time_stop_days=config.BB_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Long Straddle (Low IV)", LongStraddleStrategy(
            iv_entry_pct=config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct=config.STRADDLE_IV_EXIT_PCT,
            otm_delta=0.0,
            time_stop_days=config.STRADDLE_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Long Strangle (Low IV)", LongStraddleStrategy(
            iv_entry_pct=config.STRADDLE_IV_ENTRY_PCT,
            iv_exit_pct=config.STRADDLE_IV_EXIT_PCT,
            otm_delta=0.25,
            time_stop_days=config.STRADDLE_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("VWAP Reversion", VWAPReversionStrategy(
            vwap_window=config.VWAP_WINDOW,
            std_mult=config.VWAP_STD_MULT,
            mode="reversion",
            time_stop_days=config.VWAP_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("VWAP Breakout", VWAPReversionStrategy(
            vwap_window=config.VWAP_WINDOW,
            std_mult=config.VWAP_STD_MULT,
            mode="breakout",
            time_stop_days=config.VWAP_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Gap Fade", GapFadeStrategy(
            gap_min_pct=config.GAP_MIN_PCT,
            gap_max_pct=config.GAP_MAX_PCT,
            require_no_follow=config.GAP_REQUIRE_NO_FOLLOW,
            trend_filter=config.GAP_TREND_FILTER,
            time_stop_days=config.GAP_TIME_STOP_DAYS,
            weekly=True,
        )),
        ("Iron Condor", IronCondorStrategy(
            iv_entry_pct=config.IC_IV_ENTRY_PCT,
            iv_exit_pct=config.IC_IV_EXIT_PCT,
            body_delta=config.IC_BODY_DELTA,
            wing_delta=config.IC_WING_DELTA,
            use_adx_filter=config.IC_USE_ADX_FILTER,
            adx_choppy_threshold=config.IC_ADX_CHOPPY_THRESHOLD,
            time_stop_days=config.IC_TIME_STOP_DAYS,
            weekly=False,
        )),
    ]


# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────

TICKER_NAMES = {
    "^NSEI"               : "NIFTY 50",
    "^NSEBANK"            : "BANK NIFTY",
    "NIFTY_FIN_SERVICE.NS": "FIN NIFTY",
}

STRIKE_STEPS = {
    "^NSEI"               : 50,
    "^NSEBANK"            : 100,
    "NIFTY_FIN_SERVICE.NS": 50,
}

# Short-vol strategies sell premium — use sell SL/target params
_SHORT_VOL_LABELS = {"Short Strangle (High IV)", "Iron Condor"}
_VOLATILITY_LABELS = {
    "Short Strangle (High IV)",
    "Long Straddle (Low IV)",
    "Long Strangle (Low IV)",
    "Iron Condor",
}


def _next_trading_day(from_date: date) -> date:
    """Return the next weekday after from_date."""
    next_day = from_date + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


def _execution_note(today: date) -> str:
    """Human-readable execution date for next market open."""
    next_day = _next_trading_day(today)
    if next_day == today + timedelta(days=1):
        return f"execute {next_day.strftime('%d %b %Y')} at open"
    return f"execute next market open: {next_day.strftime('%d %b %Y')}"


# ─────────────────────────────────────────────────────────────────
# Telegram sender
# ─────────────────────────────────────────────────────────────────

def send_telegram(message: str, token: str, chat_id: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id"   : chat_id,
        "text"      : message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram send failed: {e}")
        if "resp" in locals():
            print(f"Telegram response: {resp.status_code} {resp.text}")
        return False


def _send_in_parts(messages: list[str], token: str, chat_id: str) -> bool:
    """Send a list of message parts sequentially. Returns True if all succeed."""
    success = True
    for msg in messages:
        if not send_telegram(msg, token, chat_id):
            success = False
    return success


# ─────────────────────────────────────────────────────────────────
# Signal collection
# ─────────────────────────────────────────────────────────────────

def _set_live_expiries(frame, ticker: str, today: date) -> bool:
    """Use this session's contracts, excluding expiry before next-open entry."""
    lookup = _get_chain_lookup(ticker)
    expiries = lookup.listed_expiries(today) if lookup is not None else []
    frame.attrs["contract_expiries"] = [e for e in expiries if e > today]
    return bool(frame.attrs["contract_expiries"])


def _collect_signals(
    data: dict,
    today: date,
) -> tuple[dict, list[str]]:
    """
    Run all strategies on today's data.

    Returns a nested dict:
        {
          ticker: {
            "name"  : display name,
            "spot"  : float,
            "vix"   : float,
            "atm"   : int,
            "sigs"  : [
                {
                  "strategy_label": str,
                  "direction"     : str,
                  "option_type"   : str,
                  "expiry"        : date | None,
                  "strike"        : float,
                  "meta"          : dict,
                  "is_short"      : bool,
                }
            ]
          }
        }
    """
    strategies = _all_strategies()
    collected: dict = {}
    suppressed: list[str] = []

    for strategy_label, strategy in strategies:
        for ticker, df in data.items():
            if df.empty or df.index.max().date() != today:
                reason = f"{TICKER_NAMES.get(ticker, ticker)}: current-session spot candle unavailable"
                if reason not in suppressed:
                    suppressed.append(reason)
                continue
            df_copy = df.copy()
            df_copy.attrs["ticker"] = ticker
            vix_series = df_copy.get("VIX", None)
            if vix_series is None:
                continue
            vix_source = str(df_copy.get("VIX_source", ["unknown"]).iloc[-1]) if "VIX_source" in df_copy else "unknown"
            if strategy_label in _VOLATILITY_LABELS and vix_source in {"fallback", "stale"}:
                suppressed.append(
                    f"{TICKER_NAMES.get(ticker, ticker)} {strategy_label}: VIX source is {vix_source}; volatility signal suppressed."
                )
                continue

            if not _set_live_expiries(df_copy, ticker, today):
                reason = f"{TICKER_NAMES.get(ticker, ticker)}: no current listed expiries available for next-open entry"
                if reason not in suppressed:
                    suppressed.append(reason)
                continue
            try:
                sigs = strategy.generate_signals(df_copy, vix_series, today)
            except Exception as e:
                logger.debug(f"{strategy_label} error on {ticker}: {e}")
                continue

            entry_sigs = [s for s in sigs if s.signal_type == "entry"]
            if not entry_sigs:
                continue

            if ticker not in collected:
                spot = float(df_copy["Close"].iloc[-1])
                vix  = float(df_copy["VIX"].iloc[-1]) if "VIX" in df_copy.columns else 0.0
                step = STRIKE_STEPS.get(ticker, 50)
                collected[ticker] = {
                    "name" : TICKER_NAMES.get(ticker, ticker),
                    "spot" : spot,
                    "vix"  : vix,
                    "atm"  : round(spot / step) * step,
                    "sigs" : [],
                }

            for sig in entry_sigs:
                collected[ticker]["sigs"].append({
                    "strategy_label": strategy_label,
                    "direction"     : sig.direction,
                    "option_type"   : sig.option_type,
                    "expiry"        : sig.expiry,
                    "strike"        : sig.strike,
                    "meta"          : sig.meta,
                    "group_id"      : sig.group_id,
                    "structure_type": sig.structure_type,
                    "is_short"      : sig.direction == "short",
                })

    return collected, suppressed


def _validate_contract_signal(ticker: str, sig: dict, spot: float, atm: int, today: date) -> str:
    """Return empty string when a live signal's option contract is tradable."""
    lookup = _get_chain_lookup(ticker)
    if lookup is None or not lookup.has_data_for(today):
        return "no current bhavcopy chain data for contract validation"

    expiry = sig.get("expiry")
    if not expiry:
        return "missing expiry"
    if hasattr(lookup, "has_expiry") and not lookup.has_expiry(expiry):
        return f"expiry {expiry} not found in chain"

    requested_strike = float(sig.get("strike") or atm)
    nearest = lookup.nearest_strike(requested_strike)
    max_distance = config.MAX_STRIKE_DISTANCE.get(ticker, 100.0)
    if abs(nearest - requested_strike) > max_distance:
        return f"nearest strike {nearest:g} is beyond max distance {max_distance:g}"

    if sig.get("strike") and nearest != requested_strike:
        return "exact requested strike is not listed"

    if hasattr(lookup, "premium_with_meta"):
        premium, meta = lookup.premium_with_meta(today, expiry, nearest, sig["option_type"], "close")
    else:
        premium = lookup.premium(today, expiry, nearest, sig["option_type"])
        meta = {}
    if premium is None or premium <= 0:
        return "premium is zero or unavailable"

    volume = meta.get("volume")
    oi = meta.get("oi")
    min_volume = config.MIN_OPTION_VOLUME.get(ticker, 0)
    min_oi = config.MIN_OPTION_OI.get(ticker, 0)
    if volume is None or pd.isna(volume):
        return "volume unavailable"
    if oi is None or pd.isna(oi):
        return "OI unavailable"
    if float(volume) < min_volume:
        return f"volume {float(volume):.0f} below threshold {min_volume}"
    if float(oi) < min_oi:
        return f"OI {float(oi):.0f} below threshold {min_oi}"

    sig["strike"] = nearest
    sig["meta"] = {
        **sig.get("meta", {}),
        "validation": "bhavcopy premium/OI/volume proxy; bid/ask unavailable",
        "validated_premium": round(float(premium), 2),
        "validated_volume": volume,
        "validated_oi": oi,
    }
    return ""


def _validate_collected_signals(data: dict, collected: dict, today: date) -> tuple[dict, list[str]]:
    filtered: dict = {}
    suppressed: list[str] = []
    for ticker, ticker_data in collected.items():
        valid_sigs = []
        from algo_trading.core.structures import EXPECTED_LEGS
        groups = {}
        for i, sig in enumerate(ticker_data["sigs"]):
            key = i
            if sig.get("structure_type", "single") != "single":
                key = sig.get("group_id") or (sig["strategy_label"], sig.get("expiry"))
            groups.setdefault(key, []).append(sig)
        for group in groups.values():
            structure = group[0].get("structure_type", "single")
            reasons = []
            if structure == "short_strangle" and not config.ALLOW_UNCOVERED_SHORTS:
                reasons.append("uncovered shorts disabled")
            if structure == "single" and group[0].get("direction") == "short" and not config.ALLOW_UNCOVERED_SHORTS:
                reasons.append("uncovered shorts disabled")
            if len(group) != EXPECTED_LEGS.get(structure, -1):
                reasons.append("incomplete option structure")
            reasons.extend(filter(None, (_validate_contract_signal(ticker, sig, ticker_data["spot"], ticker_data["atm"], today) for sig in group)))
            if reasons:
                if all(reason == "no current bhavcopy chain data for contract validation" for reason in reasons):
                    message = f"{ticker_data['name']}: same-session option data unavailable; all candidates blocked"
                else:
                    message = f"{ticker_data['name']} {group[0]['strategy_label']}: entire structure suppressed: {'; '.join(dict.fromkeys(reasons))}"
                if message not in suppressed:
                    suppressed.append(message)
            else:
                valid_sigs.extend(group)
        if valid_sigs:
            filtered[ticker] = {**ticker_data, "sigs": valid_sigs}
    return filtered, suppressed


# ─────────────────────────────────────────────────────────────────
# Message formatting
# ─────────────────────────────────────────────────────────────────

def _format_size_estimate(sigs: list[dict], ticker: str) -> str:
    """Size long ideas independently against full premium loss, never stop guarantees."""
    budget = config.STARTING_CAPITAL * config.RISK_PER_TRADE_PCT / 100
    lot_size = config.LOT_SIZES.get(ticker, config.DEFAULT_LOT_SIZE)
    lines = [
        f"  Budget  : ₹{budget:,.0f} ({config.RISK_PER_TRADE_PCT}% configured capital), whole trade",
        f"  Lot size: {lot_size} units per leg (configured; verify contract)",
    ]
    premiums = [s.get("meta", {}).get("validated_premium") for s in sigs]
    if (any(s.get("is_short") or s.get("direction") == "short" for s in sigs)
            or any(s.get("structure_type", "single") not in
                   ("single", "straddle", "strangle", "long_straddle", "long_strangle") for s in sigs)
            or (len(sigs) == 1 and sigs[0].get("structure_type", "single") != "single")
            or not premiums or any(not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0 for p in premiums)
            or not isinstance(lot_size, int) or lot_size <= 0):
        lines.append("  Quantity: unavailable; complete structure and premium/margin sizing required")
        return "\n".join(lines)
    debit = sum(premiums) * lot_size
    lots = max(0, math.floor(min(budget, config.STARTING_CAPITAL) / debit))
    lines.extend([
        "  Premium : " + " + ".join(f"₹{p:,.2f}" for p in premiums) + " per unit (bhavcopy close)",
        f"  Est lots: {lots}" + (" per leg, equal quantity" if len(sigs) > 1 else ""),
        f"  Quantity: {lots * lot_size} units per leg",
        f"  Max loss: ₹{lots * debit:,.2f} premium debit, before fees",
        "  Sizing  : full premium loss; stop-loss is not guaranteed",
        "  Entry   : recalculate with live ask prices, fees and available capital",
    ])
    if lots == 0:
        lines.append("  SKIP    : one lot exceeds the configured premium budget")
    return "\n".join(lines)


def _format_signal_block(sig_info: dict, spot: float, vix: float,
                          atm: int, ticker: str) -> str:
    """Format one signal into a Telegram-ready block."""
    label    = sig_info["strategy_label"]
    opt_type = sig_info["option_type"]
    is_short = sig_info["is_short"]
    meta     = sig_info["meta"]
    expiry   = sig_info["expiry"]

    # Strike: use resolved strike if set, else ATM
    strike = int(sig_info["strike"]) if sig_info["strike"] > 0 else atm

    action = ("SELL" if is_short else "BUY") + f" {opt_type}"
    expiry_str = expiry.strftime("%d %b '%y") if expiry else "—"

    # Trigger from meta
    trigger = meta.get("trigger", meta.get("reason", ""))
    if not trigger:
        if "iv_percentile" in meta:
            trigger = f"IV percentile {meta['iv_percentile']}%"
        elif "gap_pct" in meta:
            trigger = f"Gap {meta['gap_pct']:+.2f}%"
        elif "fast_ma" in meta:
            trigger = f"MA {meta.get('fast_ma', '?'):.0f} / {meta.get('slow_ma', '?'):.0f}"
        elif "rsi" in meta:
            trigger = f"RSI {meta['rsi']:.1f}"
        else:
            trigger = "signal"

    # SL/target depends on long vs short
    if is_short:
        sl_line  = f"SL      : +{config.SELL_STOP_LOSS_PCT:.0f}% of premium received"
        tgt_line = f"Target  : -{config.SELL_TARGET_PCT:.0f}% premium decay"
    else:
        sl_line  = f"SL      : -{config.BUY_STOP_LOSS_PCT:.0f}% of premium"
        tgt_line = f"Target  : +{config.BUY_TARGET_PCT:.0f}% of premium"

    label = _html_text(label)
    trigger = _html_text(trigger)

    # Iron condor leg tag
    leg = _html_text(meta.get("leg", ""))
    leg_line = f"  Leg     : {leg}\n" if leg else ""

    return (
        f"  <b>{action}</b>  [{label}]\n"
        f"  Strike  : {strike} {opt_type}   Expiry: {expiry_str}\n"
        f"  Spot    : {spot:,.0f}  |  VIX: {vix:.1f}%\n"
        f"  Trigger : {trigger}\n"
        f"{leg_line}"
        f"  {sl_line}\n"
        f"  {tgt_line}\n"
        f"{_format_size_estimate([sig_info], ticker)}"
    )


def _is_paired(sig: dict) -> bool:
    """Return True if this signal is one leg of a paired straddle/strangle trade."""
    return sig.get("meta", {}).get("paired_legs", False)


def _group_paired_signals(sigs: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Separate signals into paired (straddle/strangle legs) and standalone.

    Paired signals from the same strategy label are grouped together so they
    display as one combined block rather than two separate CE/PE signals that
    trigger a false conflict warning.

    Returns (standalone_sigs, paired_groups) where paired_groups is a list of
    dicts: {"label": str, "ce": sig, "pe": sig}.
    """
    paired_by_label: dict[str, dict] = {}
    standalone: list[dict] = []

    for sig in sigs:
        if _is_paired(sig):
            label = sig["strategy_label"]
            if label not in paired_by_label:
                paired_by_label[label] = {}
            paired_by_label[label][sig["option_type"]] = sig
        else:
            standalone.append(sig)

    paired_groups = []
    for label, legs in paired_by_label.items():
        if "CE" in legs and "PE" in legs:
            paired_groups.append({"label": label, "ce": legs["CE"], "pe": legs["PE"]})
        else:
            # Incomplete pair — treat legs as standalone to avoid hiding them
            standalone.extend(legs.values())

    return standalone, paired_groups


def _dedupe_vol_pairs(paired_groups: list[dict]) -> list[dict]:
    """
    Keep one low-IV volatility idea per underlying.

    Straddle and strangle express the same "buy volatility" view. Showing both
    makes the alert look like duplicate trades, so prefer the ATM straddle when
    both fire.
    """
    if len(paired_groups) <= 1:
        return paired_groups

    low_iv_groups = [
        g for g in paired_groups
        if g["ce"].get("meta", {}).get("pair_type") in ("straddle", "strangle")
    ]
    other_groups = [g for g in paired_groups if g not in low_iv_groups]

    if len(low_iv_groups) <= 1:
        return paired_groups

    preferred = sorted(
        low_iv_groups,
        key=lambda g: 0 if g["ce"].get("meta", {}).get("pair_type") == "straddle" else 1,
    )[0]
    return other_groups + [preferred]


def _format_paired_block(group: dict, spot: float, vix: float,
                          atm: int, ticker: str) -> str:
    """Format a straddle/strangle pair as a single combined signal block."""
    label     = group["label"]
    ce_sig    = group["ce"]
    pe_sig    = group["pe"]
    meta      = ce_sig["meta"]
    expiry    = ce_sig["expiry"]

    ce_strike = int(ce_sig["strike"]) if ce_sig["strike"] > 0 else atm
    pe_strike = int(pe_sig["strike"]) if pe_sig["strike"] > 0 else atm
    label = _html_text(label)
    pair_type = meta.get("pair_type", meta.get("variant", "straddle")).lower()
    display_pair_type = _html_text(pair_type.upper())

    expiry_str = expiry.strftime("%d %b '%y") if expiry else "—"
    trigger    = _html_text(meta.get("trigger", "vol cheap"))

    sl_line   = f"SL      : -{config.BUY_STOP_LOSS_PCT:.0f}% of premium (each leg)"
    tgt_line  = f"Target  : +{config.BUY_TARGET_PCT:.0f}% of premium (each leg)"

    if ce_strike == pe_strike:
        setup_line = "Setup   : ATM straddle - buy same strike CE and PE"
        strike_line = f"Legs    : {ce_strike} CE + {ce_strike} PE"
    else:
        setup_line = "Setup   : OTM strangle - buy higher CE and lower PE"
        strike_line = f"Legs    : {ce_strike} CE + {pe_strike} PE"

    return (
        f"  <b>BUY VOLATILITY - {display_pair_type}</b>  [{label}]\n"
        f"  View    : Seeks a move large enough to cover both premiums and costs\n"
        f"  {setup_line}\n"
        f"  Plan    : One non-directional trade; enter BOTH legs together\n"
        f"  {strike_line}\n"
        f"  Expiry  : {expiry_str}\n"
        f"  Spot    : {spot:,.0f}  |  VIX: {vix:.1f}%\n"
        f"  Trigger : {trigger}\n"
        f"  {sl_line}\n"
        f"  {tgt_line}\n"
        f"{_format_size_estimate([ce_sig, pe_sig], ticker)}"
    )


def _format_ticker_block(ticker: str, ticker_data: dict) -> str:
    """Format all signals for one underlying into a message block."""
    name  = ticker_data["name"]
    spot  = ticker_data["spot"]
    vix   = ticker_data["vix"]
    atm   = ticker_data["atm"]
    sigs  = ticker_data["sigs"]

    standalone, paired_groups = _group_paired_signals(sigs)
    paired_groups = _dedupe_vol_pairs(paired_groups)

    # True conflict: standalone strategies disagree on direction (not paired legs)
    standalone_opt_types = {s["option_type"] for s in standalone}
    has_conflict = "CE" in standalone_opt_types and "PE" in standalone_opt_types

    # Agreement among standalone signals
    ce_strategies = [s["strategy_label"] for s in standalone if s["option_type"] == "CE"]
    pe_strategies = [s["strategy_label"] for s in standalone if s["option_type"] == "PE"]

    divider = "─" * 34

    # Header
    conflict_badge = "  ⚡ CONFLICT" if has_conflict else ""
    header = f"<b>📊 {_html_text(name)}{conflict_badge}</b>"

    # Notes
    notes = []
    ideas = [[g["ce"], g["pe"]] for g in paired_groups] + [[s] for s in standalone]
    if len(ideas) > 1:
        notes.append("  Ideas are sized independently; quantities are not a combined portfolio allocation.")
    seen_contracts = set()
    overlaps = set()
    for idea in ideas:
        contracts = {(s.get("expiry"), s.get("strike") or atm, s["option_type"]) for s in idea}
        overlaps.update(seen_contracts & contracts)
        seen_contracts.update(contracts)
    if overlaps:
        names = ", ".join(f"{strike:g} {kind} ({expiry})" for expiry, strike, kind in sorted(overlaps, key=str))
        notes.append(f"  ⚠️ Overlap: {_html_text(names)} appears in multiple ideas. Taking both changes exposure; combine positions and recalculate risk before entry.")
    if paired_groups:
        pair_labels = ", ".join(_html_text(g["label"]) for g in paired_groups)
        notes.append(f"  📐 Vol trade: CE + PE is intentional, not a conflict ({pair_labels})")
    if len(ce_strategies) >= 2:
        notes.append(
            f"  ✅ {len(ce_strategies)} strategies agree: "
            f"BUY CE ({_html_text(', '.join(ce_strategies))})"
        )
    if len(pe_strategies) >= 2:
        notes.append(
            f"  ✅ {len(pe_strategies)} strategies agree: "
            f"BUY PE ({_html_text(', '.join(pe_strategies))})"
        )
    if has_conflict:
        notes.append(
            f"  ⚠️ Mixed signals — "
            f"CE from: {_html_text(', '.join(ce_strategies) or '—')} "
            f"| PE from: {_html_text(', '.join(pe_strategies) or '—')}"
        )
    note_block = "\n".join(notes)

    # Build signal blocks: paired first, then standalone
    sig_blocks = []
    for group in paired_groups:
        sig_blocks.append(_format_paired_block(group, spot, vix, atm, ticker))
    for sig_info in standalone:
        sig_blocks.append(_format_signal_block(sig_info, spot, vix, atm, ticker))

    body = f"\n\n{divider}\n".join(sig_blocks)

    parts = [header]
    if note_block:
        parts.append(note_block)
    parts.append(body)

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────
# Per-underlying message builder
# ─────────────────────────────────────────────────────────────────

_MAX_MSG_LEN = 4000  # leave some headroom below Telegram's 4096-char limit

def _build_messages(
    header: str,
    ticker_blocks: list[tuple[str, str]],  # [(ticker, formatted_block), ...]
    footer: str,
) -> list[str]:
    """
    Return one Telegram message per underlying, plus an optional summary
    header message when there are multiple underlyings.

    Layout:
      • If only one underlying: single message with header + block + footer.
      • If multiple underlyings:
          - Message 1: summary header (date, strategy count, signal count).
          - Messages 2…N: one per underlying, each with its own footer.
            If a single underlying's block still exceeds the limit it is
            chunked line-by-line as a fallback.
    """
    messages: list[str] = []

    if len(ticker_blocks) <= 1:
        # Single underlying — keep original single-message behaviour
        if ticker_blocks:
            _, block = ticker_blocks[0]
            msg = header + "\n\n" + "═" * 34 + "\n\n" + block + footer
        else:
            msg = header + footer
        messages.append(msg)
        return messages

    # Multiple underlyings — summary first, then one message per underlying
    messages.append(header)

    for ticker, block in ticker_blocks:
        body = block + footer
        if len(body) <= _MAX_MSG_LEN:
            messages.append(body)
        else:
            # Rare edge case: one underlying has so many signals it overflows.
            # Chunk by lines so no message exceeds the limit.
            chunk = ""
            for line in body.splitlines(keepends=True):
                if len(chunk) + len(line) > _MAX_MSG_LEN:
                    messages.append(chunk)
                    chunk = line
                else:
                    chunk += line
            if chunk:
                messages.append(chunk)

    return messages


# ─────────────────────────────────────────────────────────────────
# Main signal generator
# ─────────────────────────────────────────────────────────────────

def generate_signal_messages() -> list[str]:
    """
    Run all 13 strategies on today's data and return a list of
    formatted Telegram message strings (split if needed for length).

    Returns a list of strings — send each one as a separate message.
    """
    today = india_today()
    start = today - timedelta(days=400)  # enough history for slowest indicators

    # ── Weekend check ─────────────────────────────────────────────
    if today.weekday() >= 5:
        return [(
            f"<b>NSE Options Signals — {today.strftime('%d %b %Y')}</b>\n\n"
            f"Weekend — markets closed. No signals today."
        )]

    # ── Load data ─────────────────────────────────────────────────
    try:
        data = get_combined_dataset(start=start, end=today, force_refresh=False)
        data = {k: v for k, v in data.items() if k in config.UNDERLYINGS}
    except Exception as e:
        return [f"<b>Signal generation failed</b>\nData load error: {e}"]

    if not data:
        return ["<b>Signal generation failed</b>\nNo market data available."]

    # ── Staleness / VIX warnings ──────────────────────────────────
    warnings = []
    for ticker, df in data.items():
        if df.index.max().date() < today:
            warnings.append(
                f"⚠️ {TICKER_NAMES.get(ticker, ticker)} data is from "
                f"{df.index.max().date()} (not today) — signal may lag by 1 day."
            )

    from algo_trading.data.ingestion import _VIX_FALLBACK
    first_df = next(iter(data.values()))
    if "VIX" in first_df.columns:
        recent_vix = first_df["VIX"].dropna().tail(30)
        if len(recent_vix) > 1 and recent_vix.nunique() == 1:
            warnings.append(
                f"⚠️ India VIX unavailable — using fallback {_VIX_FALLBACK}%. "
                f"Options pricing is approximate."
            )

    # Fetch and validate the exact-session option archive before alert validation.
    preparation = prepare_signal_data(today)
    for ticker, item in preparation["underlyings"].items():
        if item["status"] != "ready":
            warnings.append(f"Option data: {TICKER_NAMES.get(ticker, ticker)} — {item['status']} ({today}).")

    # ── Collect all signals ───────────────────────────────────────
    collected, suppressed = _collect_signals(data, today)
    candidate_legs = sum(len(v["sigs"]) for v in collected.values())
    collected, validation_suppressed = _validate_collected_signals(data, collected, today)
    suppressed.extend(validation_suppressed)

    # ── Count active strategies ───────────────────────────────────
    n_strategies = len(_all_strategies())

    # ── Build header ──────────────────────────────────────────────
    # Count paired straddle/strangle legs as one signal each, not two
    def _count_signals(ticker_data: dict) -> int:
        standalone, paired = _group_paired_signals(ticker_data["sigs"])
        paired = _dedupe_vol_pairs(paired)
        return len(standalone) + len(paired)

    total_signals = sum(_count_signals(v) for v in collected.values())
    header = (
        f"<b>🔔 NSE Options Signals</b>\n"
        f"Date       : {today.strftime('%d %b %Y')} ({_execution_note(today)})\n"
        f"Strategies : {n_strategies} running\n"
        f"Signals    : {total_signals} across {len(collected)} underlying(s)"
    )
    if warnings:
        header += "\n\n" + "\n".join(warnings)
    if suppressed:
        header += "\n\n<b>Suppressed before alert</b>\n" + "\n".join(
            f"• {_html_text(item)}" for item in suppressed[:12]
        )

    # ── Build footer ──────────────────────────────────────────────
    footer = (
        f"\n\n{'─' * 34}\n"
        f"Execute manually on Groww at market open.\n"
        f"Set GTT stop-loss immediately after entry.\n"
        f"Validation uses same-session EOD premium/OI/volume proxies, not live bid/ask quotes."
    )

    # ── No signals case ───────────────────────────────────────────
    if not collected:
        if candidate_legs or suppressed:
            summary = "Strategy evaluation or candidate validation was blocked. This is not a no-signal market assessment."
        else:
            summary = "No strategy entry signals were generated from the available data."
        unavailable = any(item["status"] != "ready" for item in preparation["underlyings"].values())
        if unavailable:
            summary = "Option-data validation unavailable. " + summary
        action = "Retry after the same-session archive is available." if unavailable else "No entry action is indicated."
        return [header + "\n\n<b>" + summary + "</b>\nNo executable trade alert was issued.\n" + action]

    # ── Format per-ticker blocks ──────────────────────────────────
    ticker_blocks = []
    for ticker in collected:
        block = _format_ticker_block(ticker, collected[ticker])
        ticker_blocks.append((TICKER_NAMES.get(ticker, ticker), block))

    # ── Build per-underlying messages ─────────────────────────────
    return _build_messages(header, ticker_blocks, footer)


# ─────────────────────────────────────────────────────────────────
# Legacy single-message API (keeps main.py cmd_signals working)
# ─────────────────────────────────────────────────────────────────

def generate_signal_message(strategy=None) -> str:
    """
    Backward-compatible wrapper. If strategy is supplied, runs only that
    strategy (old behaviour). If None, runs all strategies (new behaviour)
    and joins parts with a separator for console display.
    """
    if strategy is not None:
        # Old path — single strategy, kept for CLI --strategy flag
        from algo_trading.strategies.combined_strategy import CombinedStrategy
        from algo_trading.strategies.inverse_strategy import InverseStrategy
        from algo_trading.strategies.trend_following import TrendFollowingStrategy
        from algo_trading.strategies.rsi_strategy import RSIStrategy

        today = india_today()
        start = today - timedelta(days=400)

        if today.weekday() >= 5:
            return (
                f"<b>NSE Options Signals — {today.strftime('%d %b %Y')}</b>\n\n"
                f"Weekend — markets closed. No signals."
            )

        try:
            data = get_combined_dataset(start=start, end=today, force_refresh=False)
            data = {k: v for k, v in data.items() if k in config.UNDERLYINGS}
        except Exception as e:
            return f"<b>Signal generation failed</b>\nData load error: {e}"

        if not data:
            return "<b>Signal generation failed</b>\nNo market data available."

        prepare_signal_data(today)
        is_inverse = isinstance(strategy, InverseStrategy)
        strategy_label = strategy.name

        grouped: dict = {}
        for ticker, df in data.items():
            if df.empty or df.index.max().date() != today:
                continue
            df.attrs["ticker"] = ticker
            if not _set_live_expiries(df, ticker, today):
                continue
            vix_series = df.get("VIX", None)
            if vix_series is None:
                continue
            vix_source = str(df.get("VIX_source", ["unknown"]).iloc[-1]) if "VIX_source" in df else "unknown"
            if strategy_label in _VOLATILITY_LABELS and vix_source in {"fallback", "stale"}:
                continue
            sigs = strategy.generate_signals(df, vix_series, today)
            entry_sigs = [s for s in sigs if s.signal_type == "entry"]
            if not entry_sigs:
                continue
            spot = float(df["Close"].iloc[-1])
            vix  = float(df["VIX"].iloc[-1]) if "VIX" in df.columns else 0.0
            step = STRIKE_STEPS.get(ticker, 50)
            atm  = round(spot / step) * step
            grouped[ticker] = {
                "name": TICKER_NAMES.get(ticker, ticker),
                "spot": spot, "vix": vix, "atm": atm,
                "sigs": [{
                    "strategy_label": strategy_label,
                    "direction": s.direction,
                    "option_type": s.option_type,
                    "expiry": s.expiry,
                    "strike": s.strike,
                    "meta": s.meta,
                    "group_id": s.group_id,
                    "structure_type": s.structure_type,
                    "is_short": s.direction == "short",
                } for s in entry_sigs],
            }

        header = (
            f"<b>NSE Options Signals</b>\n"
            f"Date    : {today.strftime('%d %b %Y')} ({_execution_note(today)})\n"
            f"Strategy: {strategy_label}"
        )
        if is_inverse:
            header += "\n⚠️ INVERSE MODE: CE/PE directions are flipped."

        if not grouped:
            return header + "\n\nNo signals today. Stay out of the market."

        grouped, suppressed = _validate_collected_signals(data, grouped, today)
        if suppressed:
            header += "\n\n<b>Suppressed before alert</b>\n" + "\n".join(
                f"• {_html_text(item)}" for item in suppressed[:12]
            )
        if not grouped:
            return header + "\n\nStrategy candidates were blocked by validation. No executable trade alert was issued."

        blocks = [_format_ticker_block(t, d) for t, d in grouped.items()]
        body   = "\n\n" + ("\n\n" + "═" * 34 + "\n\n").join(blocks)
        footer = (
            f"\n{'─' * 34}\n"
            f"Execute manually on Groww at market open.\n"
            f"Set GTT stop-loss immediately after entry."
        )
        return header + body + footer

    # New path — all strategies
    parts = generate_signal_messages()
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────
# Main — sends all-strategy messages
# ─────────────────────────────────────────────────────────────────

def main():
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        print("Export them as environment variables or add them as GitHub Secrets.")
        sys.exit(1)

    print("Generating signals from all strategies...")
    messages = generate_signal_messages()

    # Always print to console (useful in GitHub Actions logs)
    print("\n" + "="*60)
    for msg in messages:
        clean = msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        print(clean)
        print()
    print("="*60 + "\n")

    # Skip Telegram send on weekends
    if any("Weekend" in m for m in messages):
        print("Weekend — skipping Telegram notification.")
        sys.exit(0)

    print(f"Sending {len(messages)} message(s) to Telegram...")
    success = _send_in_parts(messages, token, chat_id)

    if success:
        print("Telegram notification sent successfully.")
    else:
        print("Failed to send one or more Telegram messages.")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────
# Public API / Wrapper Functions
# ─────────────────────────────────────────────────────────────────

def send_telegram_message(message: str, token: str, chat_id: str) -> bool:
    """
    Send a plain text message to Telegram.
    
    Parameters
    ----------
    message : str
        Message text (supports HTML formatting: <b>, <i>, <code>)
    token : str
        Telegram bot token
    chat_id : str
        Telegram chat ID
    
    Returns
    -------
    bool
        True if successful, False otherwise
    """
    return send_telegram(message, token, chat_id)


def send_telegram_photo(photo_path: str, caption: str, token: str, chat_id: str) -> bool:
    """
    Send a photo to Telegram (placeholder — requires additional implementation).
    
    Parameters
    ----------
    photo_path : str
        Path to photo file
    caption : str
        Caption for the photo
    token : str
        Telegram bot token
    chat_id : str
        Telegram chat ID
    
    Returns
    -------
    bool
        True if successful, False otherwise
    """
    # Placeholder — full implementation would use Telegram's sendPhoto API
    logger.warning("send_telegram_photo not fully implemented yet")
    return False


def generate_and_send_signals() -> bool:
    """
    Generate trading signals for today and send via Telegram.
    Uses environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
    
    Returns
    -------
    bool
        True if successful, False otherwise
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables")
        return False
    
    try:
        messages = generate_signal_messages()
        success = _send_in_parts(messages, token, chat_id)
        return success
    except Exception as e:
        logger.error(f"Error generating and sending signals: {e}")
        return False
