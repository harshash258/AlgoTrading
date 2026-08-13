"""
algo_trading.core.risk_manager — Position sizing and risk rule enforcement.

Determines:
  - How many lots to trade based on capital at risk
  - Whether a new trade can be opened (drawdown / position limits)
  - Whether an open trade should be stopped out or targeted
"""

import logging
from config import settings

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Stateful risk manager that tracks capital, drawdown, and open positions.

    Usage
    -----
    rm = RiskManager(starting_capital=500_000)
    if rm.can_open_trade():
        lots = rm.position_size(premium=250, lot_size=25)
        rm.register_open(trade_id)
    ...
    rm.register_close(trade_id, pnl)
    """

    def __init__(self, starting_capital: float = settings.STARTING_CAPITAL):
        self.starting_capital = starting_capital
        self.capital          = starting_capital
        self.peak_capital     = starting_capital
        self.open_positions   : set[str] = set()

    # ── Capital tracking ──────────────────────────────────────────

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak capital, as a percentage."""
        if self.peak_capital == 0:
            return 0.0
        return (self.peak_capital - self.capital) / self.peak_capital * 100.0

    def update_capital(self, pnl: float) -> None:
        """Update capital after a trade closes."""
        self.capital += pnl
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital
        logger.debug(
            f"Capital updated: ₹{self.capital:,.0f} "
            f"(peak ₹{self.peak_capital:,.0f}, dd {self.drawdown_pct:.1f}%)"
        )

    # ── Trade gating ──────────────────────────────────────────────

    def can_open_trade(self) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).

        Blocks new trades if:
          1. Max open positions reached
          2. Drawdown exceeds halt threshold
        """
        if len(self.open_positions) >= settings.MAX_OPEN_POSITIONS:
            return False, f"Max positions ({settings.MAX_OPEN_POSITIONS}) reached"

        if self.drawdown_pct >= settings.MAX_DRAWDOWN_HALT_PCT:
            return False, (
                f"Drawdown {self.drawdown_pct:.1f}% ≥ halt threshold "
                f"{settings.MAX_DRAWDOWN_HALT_PCT}%"
            )

        if self.capital <= 0:
            return False, "Capital exhausted"

        return True, "ok"

    def register_open(self, trade_id: str) -> None:
        self.open_positions.add(trade_id)

    def register_close(self, trade_id: str, pnl: float) -> None:
        self.open_positions.discard(trade_id)
        self.update_capital(pnl)

    # ── Position sizing ───────────────────────────────────────────

    def position_size(
        self,
        premium: float,
        lot_size: int,
        risk_pct: float = settings.RISK_PER_TRADE_PCT,
    ) -> int:
        """
        Calculate number of lots to trade.

        For option buying  : risk = premium × lot_size × lots (max loss if goes to 0)
        For option selling : risk = BUY_STOP_LOSS_PCT × premium received

        Returns at least 1 lot, capped so total cost ≤ available capital.
        """
        if premium <= 0 or lot_size <= 0:
            return 1

        risk_amount      = self.capital * risk_pct / 100.0
        cost_per_lot     = premium * lot_size
        lots_by_risk     = max(int(risk_amount / cost_per_lot), 1)

        # Cap by available capital (don't blow entire account on one trade)
        max_affordable   = max(int(self.capital / cost_per_lot), 1)
        lots             = min(lots_by_risk, max_affordable)

        # Hard cap — never exceed MAX_LOTS_PER_TRADE regardless of capital size
        lots             = min(lots, settings.MAX_LOTS_PER_TRADE)

        return lots

    # ── Stop / Target check ───────────────────────────────────────

    def check_exit(
        self,
        direction: str,    # "long" or "short"
        entry_premium: float,
        current_premium: float,
    ) -> tuple[bool, str]:
        """
        Check if a position should be exited based on SL/target.

        Returns (should_exit: bool, reason: str).
        """
        if entry_premium <= 0:
            return False, ""

        pnl_pct = (current_premium - entry_premium) / entry_premium * 100.0
        if direction == "short":
            pnl_pct = -pnl_pct  # for shorts, rising premium = loss

        if direction == "long":
            if pnl_pct <= -settings.BUY_STOP_LOSS_PCT:
                return True, "sl"
            if pnl_pct >= settings.BUY_TARGET_PCT:
                return True, "target"
        else:  # short
            if pnl_pct <= -settings.SELL_STOP_LOSS_PCT:
                return True, "sl"
            if pnl_pct >= settings.SELL_TARGET_PCT:
                return True, "target"

        return False, ""

    def reset(self) -> None:
        """Reset to initial state (use between backtest runs)."""
        self.capital        = self.starting_capital
        self.peak_capital   = self.starting_capital
        self.open_positions = set()
