from dataclasses import dataclass
from enum import Enum


class ZoneType(str, Enum):
    DEMAND = "DEMAND"
    SUPPLY = "SUPPLY"


@dataclass
class ZoneConfidenceContext:
    timeframe: str  # "15m", "1h", "4h"
    age_hours: float  # hours since zone formation
    impulse_atr: float  # initial reaction impulse in ATR
    test_count: int  # number of zone tests
    wick_only: bool  # zone formed only by wicks


TF_WEIGHT = {
    "1m": 5,
    "5m": 10,
    "15m": 20,
    "1h": 35,
    "4h": 60,
    "1d": 80,
}


def calculate_zone_confidence(ctx: ZoneConfidenceContext) -> int:
    score = 0

    # 1) Timeframe weight
    score += TF_WEIGHT.get(ctx.timeframe, 0)

    # 2) Freshness
    if ctx.age_hours < 24:
        score += 20
    elif ctx.age_hours < 72:
        score += 10

    # 3) Reaction strength
    if ctx.impulse_atr >= 1.5:
        score += 20
    elif ctx.impulse_atr >= 1.0:
        score += 10

    # 4) Test penalty
    if ctx.test_count == 2:
        score -= 10
    elif ctx.test_count >= 3:
        score -= 25

    # 5) Wick penalty
    if ctx.wick_only:
        score -= 20

    return max(0, min(score, 100))
