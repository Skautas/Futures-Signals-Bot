from dataclasses import dataclass


@dataclass
class PullbackResult:
    state: str
    retrace_pct: float
    reason: str


def evaluate_pullback(
    impulse_high: float,
    impulse_low: float,
    pullback_high: float,
    direction: str,
    healthy_min: float = 38,
    healthy_max: float = 61,
    overextended_min: float = 70,
) -> PullbackResult:
    """
    direction: 'BEAR' or 'BULL'
    """
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return PullbackResult(
            state="INVALID",
            retrace_pct=0.0,
            reason="Impulse range invalid"
        )

    if direction == "BEAR":
        retrace = pullback_high - impulse_low
    else:
        retrace = impulse_high - pullback_high

    retrace_pct = (retrace / impulse_range) * 100

    if retrace_pct < 20:
        return PullbackResult(
            state="NO_PULLBACK",
            retrace_pct=retrace_pct,
            reason="Price too impulsive, no correction"
        )

    if healthy_min <= retrace_pct <= healthy_max:
        return PullbackResult(
            state="HEALTHY_PULLBACK",
            retrace_pct=retrace_pct,
            reason="Ideal pullback zone"
        )

    if retrace_pct > overextended_min:
        return PullbackResult(
            state="OVEREXTENDED",
            retrace_pct=retrace_pct,
            reason="Pullback too deep, possible reversal"
        )

    return PullbackResult(
        state="MID_PULLBACK",
        retrace_pct=retrace_pct,
        reason="Neutral pullback"
    )
