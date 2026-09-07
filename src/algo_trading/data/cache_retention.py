"""Prune only the dedicated daily-signal cache, never the backtest archive."""
import re
from datetime import datetime, timedelta
from pathlib import Path
from algo_trading.data.signal_data import india_today

ROOT = Path(__file__).resolve().parents[3]


def prune_signal_cache(asof=None, days=14):
    if days < 1:
        raise ValueError("Retention must be at least one calendar day")
    asof = asof or india_today()
    target = ROOT / "data" / "signal-bhavcopy"
    # Refuse symlink/junction redirection outside the explicitly named cache.
    expected = ROOT.resolve() / "data" / "signal-bhavcopy"
    if target.resolve() != expected or target.is_symlink():
        raise ValueError("Signal cache must not redirect to another directory")
    cutoff = asof - timedelta(days=days - 1)
    removed = []
    if not target.exists():
        return removed
    for path in target.iterdir():
        if path.is_symlink() or not path.is_file() or path.resolve().parent != expected:
            continue
        match = re.fullmatch(r"fo(\d{2}[A-Z]{3}\d{4})bhav\.csv\.zip", path.name)
        if not match:
            continue
        try:
            archive_date = datetime.strptime(match[1], "%d%b%Y").date()
        except ValueError:
            continue
        if archive_date < cutoff:
            path.unlink()
            removed.append(path.name)
    return removed


if __name__ == "__main__":
    removed = prune_signal_cache()
    print(f"Removed {len(removed)} expired signal-cache archives; retained the latest 14 calendar days.")
