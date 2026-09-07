"""Directional MA signals expressed through equal-ratio defined-risk verticals."""
from algo_trading.strategies.base import BaseStrategy, Signal, register_strategy
from algo_trading.strategies.trend_following import TrendFollowingStrategy
from config import settings


class VerticalSpreadStrategy(BaseStrategy):
    structure = "bull_call_spread"
    bullish = True
    option_type = "CE"

    def __init__(self, width_steps=2, fast_ma=10, slow_ma=30):
        if not isinstance(width_steps, int) or width_steps < 1:
            raise ValueError("width_steps must be a positive integer")
        self.width_steps = width_steps
        self.signal_strategy = TrendFollowingStrategy(
            fast_ma=fast_ma, slow_ma=slow_ma, use_adx_filter=False,
            use_direction_filter=False, use_confirm=False, use_time_stop=False)
        self.active_groups = {}

    @property
    def name(self):
        return self.structure

    def generate_signals(self, data, vix, current_date):
        output = []
        for sig in self.signal_strategy.generate_signals(data, vix, current_date):
            ticker = sig.underlying
            desired = "CE" if self.bullish else "PE"
            if sig.signal_type == "exit":
                if ticker in self.active_groups:
                    output.append(Signal(current_date, ticker, "long", self.option_type,
                                         signal_type="exit", group_id=self.active_groups[ticker],
                                         structure_type=self.structure, exit_reason="signal"))
                continue
            if sig.option_type != desired:
                self.signal_strategy.on_entry_rejected(sig)
                continue
            if ticker in self.active_groups:
                continue
            step = settings.STRIKE_STEPS.get(ticker, 50)
            atm = round(float(data["Close"].iloc[-1]) / step) * step
            width = self.width_steps * step
            strikes = {
                "bull_call_spread": (atm, atm + width),
                "bear_put_spread": (atm, atm - width),
                "bull_put_spread": (atm - width, atm),
                "bear_call_spread": (atm + width, atm),
            }[self.structure]
            group = f"{ticker}:{self.name}:{current_date}"
            for direction, strike in zip(("long", "short"), strikes):
                output.append(Signal(current_date, ticker, direction, self.option_type,
                                     strike=strike, expiry=sig.expiry, group_id=group,
                                     structure_type=self.structure, meta={"leg": direction, "signal_option_type": desired}))
            self.active_groups[ticker] = group
        return output

    def on_trade_closed(self, trade):
        from types import SimpleNamespace
        self.active_groups.pop(trade.underlying, None)
        self.signal_strategy.on_trade_closed(SimpleNamespace(
            underlying=trade.underlying, option_type="CE" if self.bullish else "PE"))

    def get_params(self):
        return {"strategy": self.name, "width_steps": self.width_steps,
                "signal": self.signal_strategy.get_params()}


@register_strategy("bull_call_spread")
class BullCallSpread(VerticalSpreadStrategy):
    pass


@register_strategy("bear_put_spread")
class BearPutSpread(VerticalSpreadStrategy):
    structure, bullish, option_type = "bear_put_spread", False, "PE"


@register_strategy("bull_put_spread")
class BullPutSpread(VerticalSpreadStrategy):
    structure, bullish, option_type = "bull_put_spread", True, "PE"


@register_strategy("bear_call_spread")
class BearCallSpread(VerticalSpreadStrategy):
    structure, bullish, option_type = "bear_call_spread", False, "CE"
