from bot.market_state import MarketState
from bot.zone_resolution import ZoneResolutionState
from bot.location import Location, location_block
from bot.market_regime import detect_market_regime
from bot.regime_filters import regime_flip_block
from bot.expansion_filter import expansion_bias_block
from zone_gate import zone_gate_decision


def direction_gate(market_state):
    allow_long = False
    allow_short = False

    if market_state == MarketState.STRONG_BEAR:
        allow_short = True
    elif market_state == MarketState.STRONG_BULL:
        allow_long = True
    elif market_state == MarketState.BEAR:
        allow_short = True
    elif market_state == MarketState.BULL:
        allow_long = True
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
        allow_long, allow_short = direction_gate(market_state)

    if signal_direction == "LONG" and not allow_long:
        return False, "BLOCKED: MARKET_STATE_LONG_DISABLED", size_mult, conf_mult

    if signal_direction == "SHORT" and not allow_short:
        return False, "BLOCKED: MARKET_STATE_SHORT_DISABLED", size_mult, conf_mult

    if market_state == MarketState.RANGE and not zone_confirmation:
        return False, "BLOCKED: RANGE_REQUIRES_ZONE_CONFIRMATION", size_mult, conf_mult

    loc_ok, loc_reason = location_block(location, signal_direction)
    if not loc_ok:
        return False, loc_reason, size_mult, conf_mult

    if zone_state and zone_confidence is not None:
        zone_allowed, zone_reason = zone_gate_decision(
            zone_state=zone_state,
            zone_confidence=zone_confidence,
            resolution_confirmed=zone_confirmation,
        )
        if not zone_allowed:
            return False, f"BLOCKED: {zone_reason}", size_mult, conf_mult

    if zone_state in ("NEAR", "INSIDE") and zone_type and approach_direction:
        if zone_type == "DEMAND" and signal_direction == "SHORT" and approach_direction == "UP":
            return False, "BLOCKED: DEMAND_APPROACH_FROM_BELOW", size_mult, conf_mult
        if zone_type == "SUPPLY" and signal_direction == "LONG" and approach_direction == "DOWN":
            return False, "BLOCKED: SUPPLY_APPROACH_FROM_ABOVE", size_mult, conf_mult

    if zone_state in ("INSIDE", "NEAR") and not zone_confirmation:
        return False, "BLOCKED: ZONE_NOT_RESOLVED", size_mult, conf_mult

    if zone_resolution_state in (ZoneResolutionState.WAIT_RESOLUTION, ZoneResolutionState.INSIDE):
        return False, "BLOCKED: ZONE_RESOLUTION_WAIT", size_mult, conf_mult

    if zone_resolution_state == ZoneResolutionState.CONFIRMED_BREAK:
        if signal_direction == "LONG" and location in (Location.AT_DEMAND, Location.BELOW_DEMAND):
            return False, "BLOCKED: DEMAND_BREAKDOWN_CONFIRMED", size_mult, conf_mult
        if signal_direction == "SHORT" and location in (Location.AT_SUPPLY, Location.ABOVE_SUPPLY):
            return False, "BLOCKED: SUPPLY_BREAKOUT_CONFIRMED", size_mult, conf_mult

    if regime_ctx is not None:
        regime = detect_market_regime(regime_ctx)
        if regime_flip_block(signal_direction, regime):
            return False, f"BLOCKED_BY_REGIME ({regime})", size_mult, conf_mult
        if expansion_ctx is not None:
            if expansion_bias_block(
                signal_direction,
                expansion_ctx.get("consecutive_candles", 0),
                expansion_ctx.get("pullback_depth_pct", 0),
                expansion_ctx.get("atr_pct", 0),
            ):
                return False, "BLOCKED_BY_EXPANSION", size_mult, conf_mult

    if location == Location.AT_SUPPLY and signal_direction == "SHORT":
        if fake_breakout_result and fake_breakout_result.confirmed:
            if mode == "SWING":
                return False, "BLOCKED: OF_FAKE_BREAKOUT", size_mult, conf_mult
            if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                size_mult *= 0.5
                conf_mult *= 0.7
            else:
                return False, "BLOCKED: OF_FAKE_BREAKOUT_NEEDS_REJECTION", size_mult, conf_mult
        else:
            return False, "BLOCKED: NO_FAKE_BREAKOUT", size_mult, conf_mult
    if location == Location.AT_DEMAND and signal_direction == "LONG":
        if fake_breakout_result and fake_breakout_result.confirmed:
            if mode == "SWING":
                return False, "BLOCKED: OF_FAKE_BREAKOUT", size_mult, conf_mult
            if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                size_mult *= 0.5
                conf_mult *= 0.7
            else:
                return False, "BLOCKED: OF_FAKE_BREAKOUT_NEEDS_REJECTION", size_mult, conf_mult
        else:
            return False, "BLOCKED: NO_FAKE_BREAKOUT", size_mult, conf_mult

    if mode == "SWING":
        if signal_direction == "LONG" and not breakout_ok:
            return False, "BLOCKED: NO_SUPPLY_BREAKOUT", size_mult, conf_mult
        if signal_direction == "SHORT" and not breakout_ok:
            return False, "BLOCKED: NO_DEMAND_BREAKOUT", size_mult, conf_mult
        if location in (Location.MID_RANGE,):
            return False, "BLOCKED: MID_RANGE_SWING", size_mult, conf_mult
    else:
        if signal_direction == "LONG" and location == Location.AT_SUPPLY:
            if not breakout_ok:
                return False, "BLOCKED: NO_SUPPLY_BREAKOUT", size_mult, conf_mult

    if rejection_result and location == Location.AT_SUPPLY and signal_direction == "SHORT":
        min_score = 6 if mode == "SWING" else 4
        if rejection_result.score < min_score:
            return False, f"BLOCKED: REJECTION_{rejection_result.reason}", size_mult, conf_mult
    if rejection_result and location == Location.AT_DEMAND and signal_direction == "LONG":
        min_score = 6 if mode == "SWING" else 4
        if rejection_result.score < min_score:
            return False, f"BLOCKED: REJECTION_{rejection_result.reason}", size_mult, conf_mult

    if entry_delay_result:
        if entry_delay_result.state != "CONFIRMED":
            return False, f"BLOCKED: ENTRY_DELAY_{entry_delay_result.state}", size_mult, conf_mult

    if market_state == MarketState.STRONG_BEAR and signal_direction == "SHORT":
        if not pullback_result:
            return False, "BLOCKED: PULLBACK_MISSING", size_mult, conf_mult
        if pullback_result.state != "HEALTHY_PULLBACK":
            if pullback_result.state == "OVEREXTENDED":
                if mode == "SWING":
                    return False, "BLOCKED: PULLBACK_OVEREXTENDED_WAIT", size_mult, conf_mult
                if rejection_result and rejection_result.confirmed and entry_delay_result and entry_delay_result.state == "CONFIRMED":
                    size_mult *= 0.5
                    conf_mult *= 0.7
                else:
                    return False, "BLOCKED: PULLBACK_OVEREXTENDED_WAIT", size_mult, conf_mult
            else:
                return False, "BLOCKED: PULLBACK_NOT_READY", size_mult, conf_mult

    if rsi_value is not None:
        if signal_direction == "SHORT" and rsi_value <= 25:
            if zone_state in ("NEAR", "INSIDE"):
                return False, "BLOCKED: RSI_OVERSOLD_AT_DEMAND", size_mult, conf_mult
            if pullback_state == "ENTER" or recent_impulse:
                return False, "BLOCKED: RSI_OVERSOLD_LATE_SHORT", size_mult, conf_mult
        if signal_direction == "LONG" and rsi_value >= 75:
            if zone_state in ("NEAR", "INSIDE"):
                return False, "BLOCKED: RSI_OVERBOUGHT_AT_SUPPLY", size_mult, conf_mult
            if pullback_state == "ENTER" or recent_impulse:
                return False, "BLOCKED: RSI_OVERBOUGHT_LATE_LONG", size_mult, conf_mult

    if indicator_bias == "BLOCK_SHORT" and signal_direction == "SHORT":
        return False, "BLOCKED: INDICATOR_BLOCK_SHORT", size_mult, conf_mult
    if indicator_bias == "BLOCK_LONG" and signal_direction == "LONG":
        return False, "BLOCKED: INDICATOR_BLOCK_LONG", size_mult, conf_mult

    if mode != "CASHFLOW" and indicator_score_value < 2:
        return False, "BLOCKED: WEAK_INDICATORS", size_mult, conf_mult

    return True, "SIGNAL_ACCEPTED", size_mult, conf_mult
