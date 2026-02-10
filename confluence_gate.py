from dataclasses import dataclass
from enum import Enum


class ConfluenceDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass
class ConfluenceContext:
    score: int
    ema_stack_perfect: bool
    price_above_emas: bool
    macd_bullish: bool
    rsi: float
    atr_pct: float
    pullback_state: str  # WAIT / ARM / ENTER
    relax_level: int = 0


def evaluate_confluence_gate(ctx: ConfluenceContext) -> ConfluenceDecision:
    """
    FUND MODE:
    Confluence can NEVER override structure.
    """

    # 🚫 1. Jei dar ne ENTER stadija – joks bypass
    if ctx.pullback_state != "ENTER":
        return ConfluenceDecision.BLOCK

    relax = max(0, int(ctx.relax_level or 0))
    rsi_limit = min(80, 72 + (2 * relax))
    atr_limit = min(1.8, 1.3 + (0.1 * relax))
    score_min = max(55, 65 - (3 * relax))

    # 🚫 2. RSI per aukštas – confluence ignoruojamas
    if ctx.rsi >= rsi_limit:
        return ConfluenceDecision.BLOCK

    # 🚫 3. Per didelė volatilumas – jokių shortcutų
    if ctx.atr_pct >= atr_limit:
        return ConfluenceDecision.BLOCK

    # ✅ 4. Tik tada confluence leidžiamas
    if (
        ctx.score >= score_min
        and ctx.ema_stack_perfect
        and ctx.price_above_emas
        and ctx.macd_bullish
    ):
        return ConfluenceDecision.ALLOW

    return ConfluenceDecision.BLOCK

