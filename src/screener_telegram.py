"""
src/screener_telegram.py — Backward-compatibility shim for GitHub Actions and legacy scripts.
"""

from algo_trading.notifications.screener_bot import main

if __name__ == "__main__":
    exit(main())
