from dataclasses import dataclass
from enum import Enum


class ImpulseDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"

IMPULSE_FILTER_ENABLED = True

@dataclass
class ImpulseContext:
    candle_body_atr: float      # žvakės kūnas ATR vienetais
    candle_range_atr: float     # visa žvakė ATR vienetais
    rsi: float
    distance_from_ema_pct: float
    volume_spike: bool
    pullback_state: str         # WAIT / ARM / ENTER
    relax_level: int = 0


def evaluate_impulse_exhaustion(ctx: ImpulseContext) -> ImpulseDecision:
    """
    FUND MODE:
    Block entries on impulsive / exhausted candles.
    """

    if not IMPULSE_FILTER_ENABLED:
        return ImpulseDecision.ALLOW

    # 🚫 1. Jei dar ne ENTER – netikrinam (čia ENTRY apsauga)
    if ctx.pullback_state != "ENTER":
        return ImpulseDecision.ALLOW

    relax = max(0, int(ctx.relax_level or 0))
    body_limit = min(5.0, 3.5 + (0.4 * relax))
    range_limit = min(6.5, 4.8 + (0.5 * relax))
    rsi_limit = min(95, 90 + (1 * relax))
    ema_limit = min(4.0, 3.0 + (0.3 * relax))

    # 🚫 2. Per didelė impulsinė žvakė
    if ctx.candle_body_atr >= body_limit:
        return ImpulseDecision.BLOCK

    if ctx.candle_range_atr >= range_limit:
        return ImpulseDecision.BLOCK

    # 🚫 3. RSI per aukštas
    if ctx.rsi >= rsi_limit:
        return ImpulseDecision.BLOCK

    # 🚫 4. Kaina per toli nuo EMA (late entry)
    if ctx.distance_from_ema_pct >= ema_limit:
        return ImpulseDecision.BLOCK

    # 🚫 5. Volume climax = dažnai judėjimo pabaiga
    if ctx.volume_spike and (ctx.candle_body_atr >= 2.5 or ctx.candle_range_atr >= 3.5):
        return ImpulseDecision.BLOCK

    return ImpulseDecision.ALLOW

