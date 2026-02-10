from dataclasses import dataclass
from enum import Enum


class EntryState(str, Enum):
    WAIT = "WAIT"
    ARM = "ARM"
    ENTER = "ENTER"
    BLOCKED = "BLOCKED"


@dataclass
class PullbackContext:
    price: float
    ema_fast: float
    ema_slow: float
    vwap: float
    rsi: float
    atr_pct: float
    candle_body_pct: float
    prev_candle_body_pct: float
    volume_declining: bool
    ema_not_broken: bool
    trend_score: int
    relax_level: int = 0
    impulse_state: str = "NO_IMPULSE"
    price_above_ema: bool = False


@dataclass
class PullbackDecision:
    state: EntryState
    reason: str
    impulse_state: str = "NO_IMPULSE"


def evaluate_pullback_entry(ctx: PullbackContext) -> PullbackDecision:
    """
    PRO cashflow pullback logic (medium risk)
    Fixed:
    1. Single impulse state machine (no double blocking)
    2. ENTER after impulse only inside pullback zone
    3. Trend score as permission filter (early)
    """
    relax = max(0, int(ctx.relax_level or 0))
    rsi_limit = 1000
    atr_limit = min(3.8, 2.6 + (0.30 * relax))
    trend_min = max(3, 12 - (5 * relax))
    pullback_zone = min(1.8, 1.00 + (0.18 * relax))
    calm_body = min(0.8, 0.50 + (0.10 * relax))

    # 🚫 1. Ekstremumai – jokio entry
    if ctx.rsi >= rsi_limit:
        return PullbackDecision(
            EntryState.WAIT,
            f"RSI too high ({ctx.rsi:.1f})",
            ctx.impulse_state
        )

    if ctx.atr_pct >= atr_limit:
        return PullbackDecision(
            EntryState.WAIT,
            f"High ATR ({ctx.atr_pct:.2f}%)",
            ctx.impulse_state
        )

    # 🚫 2. Trend turi būti bent padorus
    if ctx.trend_score < trend_min:
        return PullbackDecision(
            EntryState.BLOCKED,
            "Pullback quality low",
            ctx.impulse_state
        )

    # 🔄 3. Pullback zona
    ema_mid = (ctx.ema_fast + ctx.ema_slow) / 2
    dist_to_ema = abs(ctx.price - ema_mid) / ctx.price * 100
    dist_to_vwap = abs(ctx.price - ctx.vwap) / ctx.price * 100

    in_pullback_zone = dist_to_ema <= pullback_zone or dist_to_vwap <= pullback_zone

    # Impulse state machine (single source of truth)
    impulse_state = ctx.impulse_state or "NO_IMPULSE"

    # Detect impulse
    if ctx.candle_body_pct >= 0.85:
        impulse_state = "HOT"

    # Cooldown
    if impulse_state == "HOT" and ctx.candle_body_pct <= 0.30:
        if ctx.volume_declining and ctx.ema_not_broken:
            impulse_state = "COOLING"

    # Late impulse fade entry (SAFE)
    if impulse_state == "COOLING" and ctx.price_above_ema and in_pullback_zone:
        return PullbackDecision(
            EntryState.ENTER,
            "Impulse exhausted – safe pullback entry",
            "NO_IMPULSE"
        )

    if not in_pullback_zone:
        return PullbackDecision(
            EntryState.WAIT,
            "Waiting for pullback to EMA/VWAP",
            impulse_state
        )

    # ✅ 4. Rami žvakė po pullback → ENTER
    if ctx.candle_body_pct <= calm_body:
        return PullbackDecision(
            EntryState.ENTER,
            "Pullback confirmed – cashflow entry",
            impulse_state
        )

    # 🔄 5. Pullback vyksta
    return PullbackDecision(
        EntryState.ARM,
        "Pullback forming – waiting for calm candle",
        impulse_state
    )

