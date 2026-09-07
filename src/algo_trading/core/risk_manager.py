"""
algo_trading.core.risk_manager — Position sizing and risk rule enforcement.

Determines:
  - How many lots to trade based on capital at risk
  - Whether a new trade can be opened (drawdown / position limits)
  - Whether an open trade should be stopped out or targeted
"""

import logging
import math
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
        self.reservations = {}
        self.equity = starting_capital
        self.day_start_equity = starting_capital
        self.halted = False

    # ── Capital tracking ──────────────────────────────────────────

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak capital, as a percentage."""
        if self.peak_capital == 0:
            return 0.0
        return (self.peak_capital - self.equity) / self.peak_capital * 100.0

    @property
    def available_capital(self):
        return max(0.0, min(self.capital, self.equity) - sum(v[0] for v in self.reservations.values()))

    def mark_to_market(self, unrealized):
        self.equity = self.capital + unrealized
        self.peak_capital = max(self.peak_capital, self.equity)
        if self.drawdown_pct >= settings.MAX_DRAWDOWN_HALT_PCT:
            self.halted = True

    def structure_size(self, max_loss, required_capital):
        if not all(math.isfinite(v) and v > 0 for v in (max_loss, required_capital)):
            return 0
        risk_budget = max(0, self.equity * settings.RISK_PER_TRADE_PCT / 100)
        portfolio_budget = max(0, self.equity * settings.MAX_PORTFOLIO_RISK_PCT / 100
                               - sum(v[1] for v in self.reservations.values()))
        margin_budget = max(0, self.equity * settings.MAX_MARGIN_UTILIZATION_PCT / 100
                            - sum(v[0] for v in self.reservations.values()))
        return max(0, min(int(min(risk_budget, portfolio_budget) / max_loss),
                          int(min(self.available_capital, margin_budget) / required_capital),
                          settings.MAX_LOTS_PER_TRADE))

    def update_capital(self, pnl: float) -> None:
        """Update capital after a trade closes."""
        self.capital += pnl
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
        if self.halted:
            return False, "Portfolio drawdown halt"
        if self.day_start_equity > 0 and (self.day_start_equity - self.equity) / self.day_start_equity * 100 >= settings.MAX_DAILY_LOSS_PCT:
            return False, "Daily loss limit reached"
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

    def register_open(self, trade_id: str, required_capital=0.0, max_loss=0.0) -> None:
        self.open_positions.add(trade_id)
        self.reservations[trade_id] = (required_capital, max_loss)

    def register_close(self, trade_id: str, pnl: float) -> None:
        self.open_positions.discard(trade_id)
        self.reservations.pop(trade_id, None)
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

        Returns zero when one lot exceeds the risk budget or free capital.
        """
        if not math.isfinite(premium) or premium <= 0 or lot_size <= 0:
            return 0

        risk_amount      = max(0, self.equity * risk_pct / 100.0)
        cost_per_lot     = premium * lot_size
        lots_by_risk     = max(int(risk_amount / cost_per_lot), 0)

        # Cap by available capital (don't blow entire account on one trade)
        max_affordable   = max(int(self.available_capital / cost_per_lot), 0)
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
        self.reservations = {}
        self.equity = self.starting_capital
        self.day_start_equity = self.starting_capital
        self.halted = False
