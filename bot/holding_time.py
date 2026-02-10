from dataclasses import dataclass
from bot.market_regime import MarketRegime
from bot.zone_interaction import ZoneInteraction


def estimate_holding_time(regime: MarketRegime, atr_pct: float, tp_distance_pct: float) -> dict:
    if regime in (MarketRegime.EXPANSION_UP, MarketRegime.EXPANSION_DOWN):
        base = tp_distance_pct / max(atr_pct, 0.3)
        return {
            "TP1": round(base * 0.4, 1),
            "TP2": round(base * 0.7, 1),
            "MAX": round(base * 1.0, 1)
        }

    if regime == MarketRegime.POST_CAPITULATION:
        return {
            "TP1": 1.5,
            "TP2": 3.0,
            "MAX": 5.0
        }

    base = tp_distance_pct / max(atr_pct, 0.2)
    return {
        "TP1": round(base * 0.8, 1),
        "TP2": round(base * 1.3, 1),
        "MAX": round(base * 2.0, 1)
    }


@dataclass
class HoldTimeContext:
    distance_to_target_atr: float
    avg_atr_per_hour: float
    zone_interaction: ZoneInteraction
    regime_flip: str


def estimate_hold_time(ctx: HoldTimeContext) -> dict:
    base = ctx.distance_to_target_atr / max(ctx.avg_atr_per_hour, 0.1)

    if ctx.zone_interaction == ZoneInteraction.TOUCH:
        base *= 0.7
    if ctx.zone_interaction == ZoneInteraction.BREAK_ARMED:
        base *= 1.1
    if ctx.zone_interaction == ZoneInteraction.CONFIRMED:
        base *= 1.2
    if ctx.regime_flip == "POTENTIAL_FLIP":
        base *= 0.6

    base = max(1.0, min(24.0, base))
    return {
        "TP1": round(base * 0.6, 1),
        "TP2": round(base * 0.85, 1),
        "MAX": round(base, 1),
    }
