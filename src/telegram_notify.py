"""
telegram_notify.py — Send trading signals to a Telegram chat.

Runs the combined strategy signal generation and formats a clean
Telegram message with actionable trade details.

Usage (standalone):
    python src/telegram_notify.py

Environment variables required:
    TELEGRAM_BOT_TOKEN  : Bot token from @BotFather
    TELEGRAM_CHAT_ID    : Your chat ID (get from @userinfobot)
"""

import os
import sys
import logging
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from src.data_ingestion import get_combined_dataset
from src.strategies.trend_following import TrendFollowingStrategy
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.combined_strategy import CombinedStrategy

logging.basicConfig(level=logging.WARNING)


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
        return False


# ─────────────────────────────────────────────────────────────────
# Ticker display names
# ─────────────────────────────────────────────────────────────────

TICKER_NAMES = {
    "^NSEI"      : "NIFTY 50",
    "^NSEBANK"   : "BANK NIFTY",
    "NIFTY_FIN_SERVICE.NS": "FIN NIFTY",
}

STRIKE_STEPS = {
    "^NSEI"      : 50,
    "^NSEBANK"   : 100,
    "NIFTY_FIN_SERVICE.NS": 50,
}


# ─────────────────────────────────────────────────────────────────
# Source strategy label helper
# ─────────────────────────────────────────────────────────────────

def _has_conflict(sigs: list) -> bool:
    """Return True if the signal list contains both CE and PE entries."""
    types = {s.option_type for s in sigs}
    return "CE" in types and "PE" in types


def _source_label(sig) -> str:
    """Return a short human-readable source strategy label."""
    source = sig.meta.get("source_strategy", "")
    if "rsi" in source:
        return f"RSI {config.RSI_OVERSOLD}/{config.RSI_OVERBOUGHT}"
    if "trend" in source or "ma" in source.lower():
        return f"MA {config.TREND_FAST_MA}/{config.TREND_SLOW_MA}"
    return source or "combined"


def _trigger_label(sig) -> str:
    """Return the trigger description for a signal."""
    source = sig.meta.get("source_strategy", "")
    if "rsi" in source:
        rsi_val = sig.meta.get("rsi", "")
        return f"RSI reversal ({rsi_val})"
    return "MA crossover"


# ─────────────────────────────────────────────────────────────────
# Format one signal block (preserves existing per-signal format)
# ─────────────────────────────────────────────────────────────────

def _format_signal_block(sig, spot: float, vix: float, atm: int,
                          name: str, ticker: str,
                          show_source: bool = False) -> str:
    action     = "BUY CE" if sig.option_type == "CE" else "BUY PE"
    expiry_str = sig.expiry.strftime("%d %b") if sig.expiry else "weekly"
    risk_amt   = config.STARTING_CAPITAL * config.RISK_PER_TRADE_PCT / 100
    lots_hint  = config.LOT_SIZES.get(ticker, config.DEFAULT_LOT_SIZE)

    source_line = f"  (source: {_source_label(sig)})\n" if show_source else ""

    return (
        f"<b>{action} {name}</b>\n"
        f"{source_line}"
        f"  Strike  : {atm} {sig.option_type}\n"
        f"  Expiry  : {expiry_str}\n"
        f"  Spot    : {spot:,.0f}  |  VIX: {vix:.1f}%\n"
        f"  Trigger : {_trigger_label(sig)}\n"
        f"  SL      : -{config.BUY_STOP_LOSS_PCT:.0f}% of premium\n"
        f"  Target  : +{config.BUY_TARGET_PCT:.0f}% of premium\n"
        f"  Risk    : Rs{risk_amt:,.0f} ({config.RISK_PER_TRADE_PCT}% capital)\n"
        f"  Lot size: {lots_hint}"
    )


# ─────────────────────────────────────────────────────────────────
# Generate signals and format message
# ─────────────────────────────────────────────────────────────────

def generate_signal_message() -> str:
    """
    Run the combined strategy on today's data and return a formatted
    Telegram message with conflict detection and grouping.

    - Signals grouped by underlying
    - Conflicts (CE + PE on same underlying) sorted to the top
    - Source strategy shown per signal when conflict exists
    - No-conflict days look identical to the original format
    """
    today = date.today()
    start = today - timedelta(days=400)  # enough history for MA 75

    # Skip weekends
    if today.weekday() >= 5:
        return (
            f"<b>NSE Options Signals — {today.strftime('%d %b %Y')}</b>\n\n"
            f"Weekend — markets closed. No signals."
        )

    # Load market data.
    # Don't force_refresh the full window — most of it is already cached and
    # yfinance can return empty for ^INDIAVIX on full-range requests even when
    # the incremental (recent days only) request works fine.
    # Use force_refresh=False so the cache logic runs the smarter incremental
    # path, then fall back to cached data on any download failure.
    try:
        data = get_combined_dataset(start=start, end=today, force_refresh=False)
        data = {k: v for k, v in data.items() if k in config.UNDERLYINGS}
    except Exception as e:
        return f"<b>Signal generation failed</b>\nData load error: {e}"

    if not data:
        return "<b>Signal generation failed</b>\nNo market data available."

    # Staleness / VIX fallback warnings
    stale_warning = ""
    for ticker, df in data.items():
        if df.index.max().date() < today:
            stale_warning = (
                f"\n<b>WARNING:</b> Data for {ticker} is from "
                f"{df.index.max().date()}, not today. Signal may be 1 day old."
            )
            break

    # Detect VIX fallback: all VIX values identical across the last 30 rows
    # means the constant fallback was used (no real ^INDIAVIX data available).
    from src.data_ingestion import _VIX_FALLBACK
    first_df = next(iter(data.values()))
    if "VIX" in first_df.columns:
        recent_vix = first_df["VIX"].dropna().tail(30)
        if len(recent_vix) > 1 and recent_vix.nunique() == 1:
            stale_warning += (
                f"\n<b>WARNING:</b> India VIX unavailable from Yahoo Finance. "
                f"Using fallback IV = {_VIX_FALLBACK}%. "
                f"Options pricing is approximate today."
            )

    # Build strategy
    strategy = CombinedStrategy([
        TrendFollowingStrategy(
            fast_ma=config.TREND_FAST_MA,
            slow_ma=config.TREND_SLOW_MA,
            use_adx_filter=False,
            use_confirm=False,
            use_direction_filter=False,
            use_time_stop=False,
        ),
        RSIStrategy(
            rsi_period=config.RSI_PERIOD,
            oversold=config.RSI_OVERSOLD,
            overbought=config.RSI_OVERBOUGHT,
            confirm_bars=config.RSI_CONFIRM_BARS,
            time_stop_days=config.RSI_TIME_STOP_DAYS,
            weekly=True,
        ),
    ])

    # ── Collect all entry signals grouped by ticker ───────────────
    # grouped: { ticker: { spot, vix, atm, name, sigs: [Signal, ...] } }
    grouped: dict = {}

    for ticker, df in data.items():
        df.attrs["ticker"] = ticker
        vix_series = df.get("VIX", None)
        if vix_series is None:
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
            "spot": spot,
            "vix" : vix,
            "atm" : atm,
            "name": TICKER_NAMES.get(ticker, ticker),
            "sigs": entry_sigs,
        }

    # ── Detect conflicts per ticker ───────────────────────────────
    # conflict = same underlying has both CE and PE entry signals
    conflict_tickers = [t for t, g in grouped.items() if _has_conflict(g["sigs"])]
    clean_tickers    = [t for t in grouped if t not in conflict_tickers]

    # ── Build output blocks — conflicts first ─────────────────────
    section_blocks = []

    for ticker in conflict_tickers + clean_tickers:
        g       = grouped[ticker]
        sigs    = g["sigs"]
        name    = g["name"]
        is_conf = ticker in conflict_tickers

        # Conflict header
        if is_conf:
            heading = f"<b>CONFLICT — {name}</b>"
        else:
            heading = None  # no extra heading for clean signals

        # Format each signal in this group
        # Show source always when there's a conflict; hide it when clean
        sig_lines = []
        for sig in sigs:
            block = _format_signal_block(
                sig, g["spot"], g["vix"], g["atm"],
                name, ticker,
                show_source=is_conf,
            )
            sig_lines.append(block)

        divider = "─" * 32

        if is_conf:
            group_block = (
                f"{divider}\n"
                f"{heading}\n"
                f"{divider}\n"
                + "\n\n".join(sig_lines)
            )
        else:
            group_block = "\n\n".join(sig_lines)

        section_blocks.append(group_block)

    # ── Assemble final message ────────────────────────────────────
    header = (
        f"<b>NSE Options Signals</b>\n"
        f"Date    : {today.strftime('%d %b %Y')} (execute tomorrow)\n"
        f"Strategy: MA {config.TREND_FAST_MA}/{config.TREND_SLOW_MA}"
        f" + RSI {config.RSI_OVERSOLD}/{config.RSI_OVERBOUGHT}\n"
        f"{'─' * 32}"
    )

    if not section_blocks:
        body = "\nNo signals today. Stay out of the market."
    else:
        body = "\n\n" + "\n\n".join(section_blocks)

    footer = (
        f"\n{'─' * 32}\n"
        f"Execute manually on Groww at market open.\n"
        f"Set GTT stop-loss immediately after entry."
        + stale_warning
    )

    return header + body + footer


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        print("Export them as environment variables or add them as GitHub Secrets.")
        sys.exit(1)

    print("Generating signals...")
    message = generate_signal_message()

    # Always print to console (useful in GitHub Actions logs)
    print("\n" + message.replace("<b>", "").replace("</b>", "") + "\n")

    # Skip Telegram send on weekends — no point notifying, markets closed
    if "Weekend" in message:
        print("Weekend — skipping Telegram notification.")
        sys.exit(0)

    print("Sending to Telegram...")
    success = send_telegram(message, token, chat_id)

    if success:
        print("Telegram notification sent successfully.")
    else:
        print("Failed to send Telegram notification.")
        sys.exit(1)


if __name__ == "__main__":
    main()
