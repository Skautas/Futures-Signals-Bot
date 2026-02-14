from enum import Enum

from bot.market_state import MarketState
from bot.zone_resolution import ZoneResolutionState
from bot.location import Location, location_block
from bot.market_regime import detect_market_regime
from bot.regime_filters import regime_flip_block
from bot.expansion_filter import expansion_bias_block
from zone_gate import zone_gate_decision

try:
    from config import (
        ZONE_NEAR_BLOCK_CONFIDENCE_V2,
        ZONE_SOFT_ALLOW_CONFIDENCE,
        RANGE_MIN_FINAL_SCORE,
        RANGE_ALLOW_OUTSIDE_ZONE,
        RSI_SHORT_BLOCK,
        RSI_LONG_BLOCK,
    )
except ImportError:
    ZONE_NEAR_BLOCK_CONFIDENCE_V2 = 75
    ZONE_SOFT_ALLOW_CONFIDENCE = 40
    RANGE_MIN_FINAL_SCORE = 72
    RANGE_ALLOW_OUTSIDE_ZONE = True
    RSI_SHORT_BLOCK = 25
    RSI_LONG_BLOCK = 75


class Decision(Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


def decision_engine(ctx):
    """
    ctx privalo turėti:
    - direction: LONG / SHORT
    - mode: CASHFLOW / SWING
    - market_state: BULL / BEAR / RANGE / STRONG_BEAR / STRONG_BULL (str arba MarketState)
    - rsi
    - final_score (mutable – nuobaudos keičia ctx.final_score)
    - zone_state: INSIDE / NEAR / OUTSIDE
    - zone_confidence: int 0–100
    - zone_breakout_confirmed: bool
    - fake_breakout_ok: bool

    Grąžina: (Decision.ALLOW | Decision.BLOCK, reason_str)
    """
    # Normalize market_state to string
    ms = getattr(ctx.market_state, "name", None) or getattr(ctx.market_state, "value", None) or str(ctx.market_state)

    # --------------------------------------------------
    # 1. RSI VETO (ABSOLIUTUS)
    # --------------------------------------------------
    if ctx.direction == "SHORT" and ctx.rsi is not None and ctx.rsi <= RSI_SHORT_BLOCK:
        return Decision.BLOCK, "RSI_OVERSOLD"

    if ctx.direction == "LONG" and ctx.rsi is not None and ctx.rsi >= RSI_LONG_BLOCK:
        return Decision.BLOCK, "RSI_OVERBOUGHT"

    # --------------------------------------------------
    # 2. MARKET STATE GATE (SOFT)
    # --------------------------------------------------
    if ms in ("BEAR", "STRONG_BEAR") and ctx.direction == "LONG":
        if ctx.mode == "SWING":
            return Decision.BLOCK, "MARKET_STATE_LONG_DISABLED"
        ctx.final_score -= 20

    if ms in ("BULL", "STRONG_BULL") and ctx.direction == "SHORT":
        if ctx.mode == "SWING":
            return Decision.BLOCK, "MARKET_STATE_SHORT_DISABLED"
        ctx.final_score -= 20

    # --------------------------------------------------
    # 3. ZONE POSITION LOGIC
    # --------------------------------------------------
    zone_state = getattr(ctx, "zone_state", None) or "OUTSIDE"
    zone_conf = getattr(ctx, "zone_confidence", 0) or 0

    if zone_state == "INSIDE":
        return Decision.BLOCK, "PRICE_INSIDE_ZONE"

    if zone_state == "NEAR":
        if zone_conf >= ZONE_NEAR_BLOCK_CONFIDENCE_V2:
            return Decision.BLOCK, "STRONG_ZONE_NEAR"
        ctx.final_score -= 10  # SOFT penalty

    # --------------------------------------------------
    # 4. SUPPLY / DEMAND BREAKOUT GATE
    # --------------------------------------------------
    breakout_ok = getattr(ctx, "zone_breakout_confirmed", False)

    if ctx.direction == "LONG" and not breakout_ok:
        if ctx.mode == "SWING":
            return Decision.BLOCK, "NO_SUPPLY_BREAKOUT"
        ctx.final_score -= 30

    if ctx.direction == "SHORT" and not breakout_ok:
        if ctx.mode == "SWING":
            return Decision.BLOCK, "NO_DEMAND_BREAKOUT"
        ctx.final_score -= 30

    # --------------------------------------------------
    # 5. FAKE BREAKOUT FILTER
    # --------------------------------------------------
    fake_ok = getattr(ctx, "fake_breakout_ok", True)
    if not fake_ok:
        if ctx.mode == "SWING":
            return Decision.BLOCK, "NO_FAKE_BREAKOUT"
        ctx.final_score -= 15

    # --------------------------------------------------
    # 6. RANGE MODE SPECIAL LOGIC
    # --------------------------------------------------
    if ms == "RANGE":
        if zone_conf < ZONE_SOFT_ALLOW_CONFIDENCE:
            if not RANGE_ALLOW_OUTSIDE_ZONE:
                return Decision.BLOCK, "RANGE_REQUIRES_ZONE_CONFIRMATION"
        ctx.final_score -= 10

    # --------------------------------------------------
    # 7. FINAL SCORE CHECK
    # --------------------------------------------------
    if ctx.final_score < RANGE_MIN_FINAL_SCORE:
        return Decision.BLOCK, "FINAL_SCORE_TOO_LOW"

    # --------------------------------------------------
    # 8. ✅ ALLOW SIGNAL
    # --------------------------------------------------
    return Decision.ALLOW, "SIGNAL_ACCEPTED"


def tp_multiplier(zone_confidence):
    """
    TP adaptacija pagal zonos stiprumą.
    Grąžina: 1.0 | 0.8 | 0.6 | 0.5
    """
    if zone_confidence is None:
        return 0.5
    if zone_confidence >= 80:
        return 1.0
    elif zone_confidence >= 60:
        return 0.8
    elif zone_confidence >= 40:
        return 0.6
    else:
        return 0.5


def direction_gate(market_state, mode="CASHFLOW"):
    """SWING=strict (trend only). CASHFLOW=flexible (allows counter-trend)."""
    allow_long = False
    allow_short = False

    if market_state == MarketState.STRONG_BEAR:
        allow_short = True
        if mode == "CASHFLOW":
            allow_long = True  # CASHFLOW flexible; check_all_filters enforces RSI<35 etc
    elif market_state == MarketState.STRONG_BULL:
        allow_long = True
        if mode == "CASHFLOW":
            allow_short = True  # CASHFLOW flexible
    elif market_state == MarketState.BEAR:
        allow_short = True
        if mode == "CASHFLOW":
            allow_long = True
    elif market_state == MarketState.BULL:
        allow_long = True
        if mode == "CASHFLOW":
            allow_short = True
    elif market_state == MarketState.RANGE:
        allow_long = True
        allow_short = True

    return allow_long, allow_short


def evaluate_signal(
    market_state,
    location,
    signal_direction,
    breakout_ok,
    indicator_score_value,
    indicator_bias=None,
    rsi_value=None,
    pullback_state=None,
    recent_impulse=False,
    pullback_result=None,
    rejection_result=None,
    fake_breakout_result=None,
    entry_delay_result=None,
    zone_resolution_state=None,
    zone_state=None,
    zone_confidence=None,
    zone_confirmation=False,
    zone_type=None,
    approach_direction=None,
    mode="CASHFLOW",
    regime_ctx=None,
    expansion_ctx=None,
):
    # GATE ORDER (DO NOT REORDER):
    # 1. Market State
    # 2. Direction Gate
    # 3. Location (Supply/Demand proximity)
    # 4. Zone Resolution (CONFIRMED ONLY)
    # 5. Rejection / Breakout Logic
    # 6. Indicators (soft)
    size_mult = 1.0
    conf_mult = 1.0

    if mode == "SWING":
        if market_state == MarketState.STRONG_BULL:
            allow_long, allow_short = True, False
        elif market_state == MarketState.STRONG_BEAR:
            allow_long, allow_short = False, True
        else:
            allow_long, allow_short = False, False
    else:
        allow_long, allow_short = direction_gate(market_state, mode)

    if signal_direction == "LONG" and not allow_long:
        return False, "BLOCKED: MARKET_STATE_LONG_DISABLED", size_mult, conf_mult, 1.0

    if signal_direction == "SHORT" and not allow_short:
        return False, "BLOCKED: MARKET_STATE_SHORT_DISABLED", size_mult, conf_mult, 1.0

    # RANGE: zone is BONUS, not gate. Score >= 72 allows trade even without zone (checked in futures_signals).
    # Both CASHFLOW and SWING pass here; RANGE_SCORE_TOO_LOW blocks only when zone_conf<40 AND score<72.
    # (previously blocked SWING with RANGE_REQUIRES_ZONE_CONFIRMATION)

    loc_ok, loc_reason, loc_tp_mod = location_block(
        location, signal_direction, zone_confidence=zone_confidence or 0, mode=mode
    )
    if not loc_ok:
        return False, loc_reason, size_mult, conf_mult, 1.0

    if zone_state and zone_confidence is not None:
        zone_allowed, zone_reason = zone_gate_decision(
            zone_state=zone_state,
            zone_confidence=zone_confidence,
            resolution_confirmed=zone_confirmation,
            mode=mode,
        )
        if not zone_allowed:
            return False, f"BLOCKED: {zone_reason}", size_mult, conf_mult, 1.0

    if zone_state in ("NEAR", "INSIDE") and zone_type and approach_direction:
        if zone_type == "DEMAND" and signal_direction == "SHORT" and approach_direction == "UP":
            return False, "BLOCKED: DEMAND_APPROACH_FROM_BELOW", size_mult, conf_mult, 1.0
        if zone_type == "SUPPLY" and signal_direction == "LONG" and approach_direction == "DOWN":
            return False, "BLOCKED: SUPPLY_APPROACH_FROM_ABOVE", size_mult, conf_mult, 1.0

    if zone_state in ("INSIDE", "NEAR") and not zone_confirmation:
        return False, "BLOCKED: ZONE_NOT_RESOLVED", size_mult, conf_mult, 1.0

    if zone_resolution_state in (ZoneResolutionState.WAIT_RESOLUTION, ZoneResolutionState.INSIDE):
        return False, "BLOCKED: ZONE_RESOLUTION_WAIT", size_mult, conf_mult, 1.0

    if zone_resolution_state == ZoneResolutionState.CONFIRMED_BREAK:
        if signal_direction == "LONG" and location in (Location.AT_DEMAND, Location.BELOW_DEMAND):
            return False, "BLOCKED: DEMAND_BREAKDOWN_CONFIRMED", size_mult, conf_mult, 1.0
        if signal_direction == "SHORT" and location in (Location.AT_SUPPLY, Location.ABOVE_SUPPLY):
            return False, "BLOCKED: SUPPLY_BREAKOUT_CONFIRMED", size_mult, conf_mult, 1.0

    if regime_ctx is not None:
        regime = detect_market_regime(regime_ctx)
        if regime_flip_block(signal_direction, regime):
            return False, f"BLOCKED_BY_REGIME ({regime})", size_mult, conf_mult, 1.0
        if expansion_ctx is not None:
            if expansion_bias_block(
                signal_direction,
                expansion_ctx.get("consecutive_candles", 0),
                expansion_ctx.get("pullback_depth_pct", 0),
                expansion_ctx.get("atr_pct", 0),
            ):
                return False, "BLOCKED_BY_EXPANSION", size_mult, conf_mult, 1.0

    if location == Location.AT_SUPPLY and signal_direction == "SHORT":
        if fake_breakout_result and fake_breakout_result.confirmed:
            if mode == "SWING":
                return False, "BLOCKED: OF_FAKE_BREAKOUT", size_mult, conf_mult, 1.0
            if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                size_mult *= 0.5
                conf_mult *= 0.7
            else:
                return False, "BLOCKED: OF_FAKE_BREAKOUT_NEEDS_REJECTION", size_mult, conf_mult, 1.0
        else:
            # NO_FAKE_BREAKOUT: SWING=block; CASHFLOW=WAIT/CONFIRMATION (allow with reduced size)
            if mode == "SWING":
                return False, "BLOCKED: NO_FAKE_BREAKOUT", size_mult, conf_mult, 1.0
            size_mult *= 0.6
            conf_mult *= 0.75
    if location == Location.AT_DEMAND and signal_direction == "LONG":
        if fake_breakout_result and fake_breakout_result.confirmed:
            if mode == "SWING":
                return False, "BLOCKED: OF_FAKE_BREAKOUT", size_mult, conf_mult, 1.0
            if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                size_mult *= 0.5
                conf_mult *= 0.7
            else:
                return False, "BLOCKED: OF_FAKE_BREAKOUT_NEEDS_REJECTION", size_mult, conf_mult, 1.0
        else:
            # NO_FAKE_BREAKOUT: SWING=block; CASHFLOW=WAIT/CONFIRMATION (allow with reduced size)
            if mode == "SWING":
                return False, "BLOCKED: NO_FAKE_BREAKOUT", size_mult, conf_mult, 1.0
            size_mult *= 0.6
            conf_mult *= 0.75

    if mode == "SWING":
        if signal_direction == "LONG" and not breakout_ok:
            return False, "BLOCKED: NO_SUPPLY_BREAKOUT", size_mult, conf_mult, 1.0
        if signal_direction == "SHORT" and not breakout_ok:
            return False, "BLOCKED: NO_DEMAND_BREAKOUT", size_mult, conf_mult, 1.0
        if location in (Location.MID_RANGE,):
            return False, "BLOCKED: MID_RANGE_SWING", size_mult, conf_mult, 1.0
    else:
        if signal_direction == "LONG" and location == Location.AT_SUPPLY:
            if not breakout_ok:
                return False, "BLOCKED: NO_SUPPLY_BREAKOUT", size_mult, conf_mult, 1.0

    if rejection_result and location == Location.AT_SUPPLY and signal_direction == "SHORT":
        min_score = 6 if mode == "SWING" else 4
        if rejection_result.score < min_score:
            return False, f"BLOCKED: REJECTION_{rejection_result.reason}", size_mult, conf_mult, 1.0
    if rejection_result and location == Location.AT_DEMAND and signal_direction == "LONG":
        min_score = 6 if mode == "SWING" else 4
        if rejection_result.score < min_score:
            return False, f"BLOCKED: REJECTION_{rejection_result.reason}", size_mult, conf_mult, 1.0

    if entry_delay_result:
        if entry_delay_result.state != "CONFIRMED":
            return False, f"BLOCKED: ENTRY_DELAY_{entry_delay_result.state}", size_mult, conf_mult, 1.0

    if market_state == MarketState.STRONG_BEAR and signal_direction == "SHORT":
        if not pullback_result:
            return False, "BLOCKED: PULLBACK_MISSING", size_mult, conf_mult, 1.0
        if pullback_result.state != "HEALTHY_PULLBACK":
            if pullback_result.state == "OVEREXTENDED":
                if mode == "SWING":
                    return False, "BLOCKED: PULLBACK_OVEREXTENDED_WAIT", size_mult, conf_mult, 1.0
                if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                    size_mult *= 0.5
                    conf_mult *= 0.7
                else:
                    return False, "BLOCKED: PULLBACK_OVEREXTENDED_WAIT", size_mult, conf_mult, 1.0
            else:
                return False, "BLOCKED: PULLBACK_NOT_READY", size_mult, conf_mult, 1.0

    # RSI veto – only extreme; mode-dependent. Inactive if zone_confidence < 40
    if rsi_value is not None and (zone_confidence or 0) >= 40:
        short_veto = 25 if mode == "CASHFLOW" else 30
        long_veto = 75 if mode == "CASHFLOW" else 70
        if signal_direction == "SHORT" and rsi_value <= short_veto:
            if zone_state in ("NEAR", "INSIDE"):
                return False, "BLOCKED: RSI_OVERSOLD_AT_DEMAND", size_mult, conf_mult, 1.0
            if pullback_state == "ENTER" or recent_impulse:
                return False, "BLOCKED: RSI_OVERSOLD_LATE_SHORT", size_mult, conf_mult, 1.0
        if signal_direction == "LONG" and rsi_value >= long_veto:
            if zone_state in ("NEAR", "INSIDE"):
                return False, "BLOCKED: RSI_OVERBOUGHT_AT_SUPPLY", size_mult, conf_mult, 1.0
            if pullback_state == "ENTER" or recent_impulse:
                return False, "BLOCKED: RSI_OVERBOUGHT_LATE_LONG", size_mult, conf_mult, 1.0

    if indicator_bias == "BLOCK_SHORT" and signal_direction == "SHORT":
        return False, "BLOCKED: INDICATOR_BLOCK_SHORT", size_mult, conf_mult, 1.0
    if indicator_bias == "BLOCK_LONG" and signal_direction == "LONG":
        return False, "BLOCKED: INDICATOR_BLOCK_LONG", size_mult, conf_mult, 1.0

    if mode != "CASHFLOW" and indicator_score_value < 2:
        return False, "BLOCKED: WEAK_INDICATORS", size_mult, conf_mult, 1.0

    return True, "SIGNAL_ACCEPTED", size_mult, conf_mult, loc_tp_mod
