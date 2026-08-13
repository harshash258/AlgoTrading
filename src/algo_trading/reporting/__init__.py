"""
algo_trading.reporting package — Metrics computation and HTML reporting.
"""

from algo_trading.reporting.metrics import (
    compute_metrics,
    trades_to_dataframe,
    print_summary,
)
from algo_trading.reporting.html_generator import (
    generate_html_report,
)

__all__ = [
    "compute_metrics",
    "trades_to_dataframe",
    "print_summary",
    "generate_html_report",
]
