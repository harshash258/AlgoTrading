"""
src/telegram_notify.py — Backward-compatibility shim for GitHub Actions and legacy scripts.
"""

from algo_trading.notifications.telegram import main

if __name__ == "__main__":
    main()
