from enum import Enum
from dataclasses import dataclass


class MarketRegime(str, Enum):
    TREND_DOWN = "TREND_DOWN"
    TREND_UP = "TREND_UP"
    RANGE = "RANGE"
    EXPANSION_UP = "EXPANSION_UP"
    EXPANSION_DOWN = "EXPANSION_DOWN"
    POST_CAPITULATION = "POST_CAPITULATION"


@dataclass
class RegimeContext:
    rsi: float
    atr_pct: float
    impulse_body_atr: float
    volume_spike: bool
    consecutive_same_dir: int
    structure_broken: bool
    close_above_midrange: bool
    close_below_midrange: bool


def detect_market_regime(ctx: RegimeContext) -> MarketRegime:
    if (
        ctx.rsi < 30
        and ctx.impulse_body_atr >= 2.8
        and ctx.volume_spike
        and ctx.close_above_midrange
    ):
        return MarketRegime.POST_CAPITULATION

    if ctx.consecutive_same_dir >= 4 and ctx.atr_pct >= 1.2:
        if ctx.close_above_midrange:
            return MarketRegime.EXPANSION_UP
        if ctx.close_below_midrange:
            return MarketRegime.EXPANSION_DOWN

    if ctx.structure_broken:
        return MarketRegime.TREND_DOWN if ctx.close_below_midrange else MarketRegime.TREND_UP

    return MarketRegime.RANGE
