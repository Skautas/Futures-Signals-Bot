from dataclasses import dataclass
from bot.market_state import MarketState


@dataclass
class HTFContext:
    near_daily_resistance: bool
    near_daily_support: bool
    near_weekly_resistance: bool
    near_weekly_support: bool
    distance_to_nearest_resistance: float
    distance_to_nearest_support: float
    dominant_barrier: str


def evaluate_htf_sr_gate(mode, market_state, direction, ctx, breakout_confirmed_daily, breakout_confirmed_weekly):
    size_mult = 1.0
    conf_mult = 1.0

    if direction == "LONG":
        if ctx.near_weekly_resistance and not breakout_confirmed_weekly:
            return False, "HTF_WEEKLY_RESISTANCE", size_mult, conf_mult
        if ctx.near_daily_resistance and not breakout_confirmed_daily and market_state != MarketState.STRONG_BULL:
            if mode == "CASHFLOW":
                size_mult *= 0.7
                conf_mult *= 0.85
            else:
                return False, "HTF_DAILY_RESISTANCE", size_mult, conf_mult

    if direction == "SHORT":
        if ctx.near_weekly_support and not breakout_confirmed_weekly:
            return False, "HTF_WEEKLY_SUPPORT", size_mult, conf_mult
        if ctx.near_daily_support and not breakout_confirmed_daily and market_state != MarketState.STRONG_BEAR:
            if mode == "CASHFLOW":
                size_mult *= 0.7
                conf_mult *= 0.85
            else:
                return False, "HTF_DAILY_SUPPORT", size_mult, conf_mult

    return True, "HTF_SR_OK", size_mult, conf_mult
