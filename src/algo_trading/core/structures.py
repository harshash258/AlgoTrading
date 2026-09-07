"""Structure validation and exact piecewise-linear expiry risk for cash options."""
import math


EXPECTED_LEGS = {"single": 1, "straddle": 2, "strangle": 2,
                 "long_straddle": 2, "long_strangle": 2, "short_strangle": 2,
                 "iron_condor": 4, "bull_call_spread": 2, "bear_put_spread": 2,
                 "bull_put_spread": 2, "bear_call_spread": 2}


def validate_structure(legs, structure_type):
    if not legs or len(legs) != EXPECTED_LEGS.get(structure_type, -1):
        raise ValueError(f"Incomplete or unsupported structure: {structure_type}")
    if len({(l.underlying, l.expiry, l.lot_size) for l in legs}) != 1:
        raise ValueError("Legs must share underlying, expiry and lot size")
    if len({(l.strike, l.option_type) for l in legs}) != len(legs):
        raise ValueError("Duplicate contracts in structure")
    if any(l.strike <= 0 or not math.isfinite(l.entry_premium) or l.entry_premium <= 0 for l in legs):
        raise ValueError("Invalid strike or premium")
    if structure_type == "iron_condor":
        ordered = sorted(legs, key=lambda l: l.strike)
        if [(l.direction, l.option_type) for l in ordered] != [
            ("long", "PE"), ("short", "PE"), ("short", "CE"), ("long", "CE")
        ] or len({l.strike for l in legs}) != 4:
            raise ValueError("Iron condor wings must protect both short strikes")
    if structure_type.endswith("_spread"):
        ordered = sorted(legs, key=lambda l: l.strike)
        expected = {
            "bull_call_spread": [("long", "CE"), ("short", "CE")],
            "bear_put_spread": [("short", "PE"), ("long", "PE")],
            "bull_put_spread": [("long", "PE"), ("short", "PE")],
            "bear_call_spread": [("short", "CE"), ("long", "CE")],
        }[structure_type]
        if [(l.direction, l.option_type) for l in ordered] != expected:
            raise ValueError("Invalid vertical spread orientation")
    if "straddle" in structure_type or "strangle" in structure_type:
        side = "short" if structure_type == "short_strangle" else "long"
        if {l.option_type for l in legs} != {"CE", "PE"} or any(l.direction != side for l in legs):
            raise ValueError("Invalid volatility structure")
        call = next(l for l in legs if l.option_type == "CE")
        put = next(l for l in legs if l.option_type == "PE")
        if ("straddle" in structure_type and call.strike != put.strike) or call.strike < put.strike:
            raise ValueError("Invalid straddle/strangle strikes")


def expiry_risk(legs):
    """Return max loss and cash debit for ONE equal-ratio lot, including entry fees.

    Uncovered calls have infinite maximum loss. Uncovered puts have their exact
    loss at zero. Margin remains a separate pre-trade constraint.
    """
    debit = sum((1 if l.direction == "long" else -1) * l.entry_premium * l.lot_size for l in legs)
    fees = sum(l.entry_cost for l in legs)
    def payoff(spot):
        return sum((1 if l.direction == "long" else -1) * l.lot_size *
                   max((spot - l.strike) if l.option_type == "CE" else (l.strike - spot), 0)
                   for l in legs) - debit - fees
    tail_slope = sum((1 if l.direction == "long" else -1) * l.lot_size for l in legs if l.option_type == "CE")
    loss = math.inf if tail_slope < 0 else max(0.0, -min(payoff(s) for s in [0.0] + [l.strike for l in legs]))
    return loss, max(debit, 0.0) + fees
