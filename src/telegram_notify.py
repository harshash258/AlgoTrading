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
    "NIFTYFIN.NS": "FIN NIFTY",
}

STRIKE_STEPS = {
    "^NSEI"      : 50,
    "^NSEBANK"   : 100,
    "NIFTYFIN.NS": 50,
}


# ─────────────────────────────────────────────────────────────────
# Generate signals and format message
# ─────────────────────────────────────────────────────────────────

def generate_signal_message() -> str:
    """
    Run the combined strategy on today's data and return a
    formatted Telegram message string.
    """
    today = date.today()
    start = today - timedelta(days=400)  # enough history for MA 75

    # Skip weekends — markets closed
    if today.weekday() >= 5:
        return (
            f"<b>NSE Options Signals — {today.strftime('%d %b %Y')}</b>\n\n"
            f"Weekend — markets closed. No signals."
        )

    # Load market data
    try:
        data = get_combined_dataset(start=start, end=today, force_refresh=True)
        data = {k: v for k, v in data.items() if k in config.UNDERLYINGS}
    except Exception as e:
        return f"<b>Signal generation failed</b>\nData load error: {e}"

    if not data:
        return "<b>Signal generation failed</b>\nNo market data available."

    # Verify data is fresh — warn if most recent candle is not today
    stale_warning = ""
    for ticker, df in data.items():
        latest = df.index.max().date()
        if latest < today:
            stale_warning = f"\n<b>WARNING:</b> Data for {ticker} is from {latest}, not today. Signal may be 1 day old."
            break

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

    signal_blocks = []

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
        name = TICKER_NAMES.get(ticker, ticker)

        for sig in entry_sigs:
            action     = "BUY CE" if sig.option_type == "CE" else "BUY PE"
            expiry_str = sig.expiry.strftime("%d %b") if sig.expiry else "weekly"

            # Estimate premium context from meta if available
            source = sig.meta.get("source_strategy", "")
            if "rsi" in source:
                rsi_val = sig.meta.get("rsi", "")
                trigger = f"RSI reversal ({rsi_val})"
            else:
                trigger = "MA crossover"

            # Position sizing hint
            risk_amt   = config.STARTING_CAPITAL * config.RISK_PER_TRADE_PCT / 100
            lots_hint  = config.LOT_SIZES.get(ticker, config.DEFAULT_LOT_SIZE)

            block = (
                f"<b>{action} {name}</b>\n"
                f"  Strike  : {atm} {sig.option_type}\n"
                f"  Expiry  : {expiry_str}\n"
                f"  Spot    : {spot:,.0f}  |  VIX: {vix:.1f}%\n"
                f"  Trigger : {trigger}\n"
                f"  SL      : -{config.BUY_STOP_LOSS_PCT:.0f}% of premium\n"
                f"  Target  : +{config.BUY_TARGET_PCT:.0f}% of premium\n"
                f"  Risk    : Rs{risk_amt:,.0f} ({config.RISK_PER_TRADE_PCT}% capital)\n"
                f"  Lot size: {lots_hint}"
            )
            signal_blocks.append(block)

    tomorrow = today + timedelta(days=1)
    # Skip to Monday if tomorrow is weekend
    if tomorrow.weekday() == 5:
        tomorrow += timedelta(days=2)
    elif tomorrow.weekday() == 6:
        tomorrow += timedelta(days=1)

    header = (
        f"<b>NSE Options Signals</b>\n"
        f"Date    : {today.strftime('%d %b %Y')} (execute tomorrow)\n"
        f"Strategy: MA {config.TREND_FAST_MA}/{config.TREND_SLOW_MA} + RSI {config.RSI_OVERSOLD}/{config.RSI_OVERBOUGHT}\n"
        f"{'─' * 32}"
    )

    if not signal_blocks:
        body = "\nNo signals today. Stay out of the market."
    else:
        body = "\n\n" + "\n\n".join(signal_blocks)

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

    print("Sending to Telegram...")
    success = send_telegram(message, token, chat_id)

    if success:
        print("Telegram notification sent successfully.")
    else:
        print("Failed to send Telegram notification.")
        sys.exit(1)


if __name__ == "__main__":
    main()
