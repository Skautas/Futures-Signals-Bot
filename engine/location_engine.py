from dataclasses import dataclass


@dataclass
class LocationDecision:
    allow_long: bool
    allow_short: bool
    reason: str
    position_multiplier: float = 1.0


class LocationEngine:
    def evaluate(self, ctx) -> LocationDecision:
        allow_long = True
        allow_short = True
        reason = "OK"
        position_multiplier = 1.0
        bearish_impulse_flag = bool(getattr(ctx, "bearish_impulse", False))
        resistance_break = bool(getattr(ctx, "resistance_break", False))
        zone_accepted = bool(getattr(ctx, "zone_accepted", False))
        next_candle_bullish = bool(getattr(ctx, "next_candle_bullish", False))
        supply_override = zone_accepted and next_candle_bullish
        supply_rejection_guard = bool(getattr(ctx, "supply_rejection_guard", False))

        # 🔒 HTF BOS LOCK
        if ctx.htf_bos == "BULLISH":
            allow_short = False
            reason = "HTF_BOS_BULLISH"

        if ctx.htf_bos == "BEARISH":
            allow_long = False
            reason = "HTF_BOS_BEARISH"

        # 🚫 SUPPLY / DEMAND LOCATION FILTER
        # Highest priority: near supply/demand guard (late entry protection)
        if ctx.near_supply_zone and not ctx.htf_supply_breakout:
            if not supply_override:
                allow_long = False
                reason = "NEAR_SUPPLY_NO_BREAKOUT"

        if ctx.near_demand_zone and not ctx.htf_demand_breakdown:
            if bearish_impulse_flag:
                allow_short = True
                position_multiplier = min(position_multiplier, 0.7)
                reason = "NEAR_DEMAND_BEARISH_IMPULSE_REDUCED"
            else:
                allow_short = False
                reason = "NEAR_DEMAND_NO_BREAKDOWN"

        # Inside-zone hard blocks (secondary)
        if ctx.in_supply_zone and not ctx.htf_supply_breakout:
            if not supply_override:
                allow_long = False
                reason = "INSIDE_SUPPLY_NO_BREAKOUT"

        if ctx.in_demand_zone and not ctx.htf_demand_breakdown:
            if bearish_impulse_flag:
                allow_short = True
                position_multiplier = min(position_multiplier, 0.7)
                reason = "INSIDE_DEMAND_BEARISH_IMPULSE_REDUCED"
            else:
                allow_short = False
                reason = "INSIDE_DEMAND_NO_BREAKDOWN"

        # ZONE ACCEPTANCE (LONG) - post-impulse check
        if resistance_break and not zone_accepted:
            allow_long = False
            reason = "SUPPLY_BREAK_NOT_ACCEPTED"
        elif supply_override:
            allow_long = True
            reason = "SUPPLY_ACCEPTED_LONG"

        # Supply rejection guard (extra LONG block)
        if supply_rejection_guard:
            allow_long = False
            reason = "SUPPLY_REJECTION_GUARD"

        return LocationDecision(
            allow_long=allow_long,
            allow_short=allow_short,
            reason=reason,
            position_multiplier=position_multiplier,
        )
