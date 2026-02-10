from dataclasses import dataclass
from datetime import datetime


@dataclass
class FakeBreakoutResult:
    confirmed: bool
    score: int
    reason: str
    details: str = ""


def _is_session_ok():
    # UTC hours: London ~ 07-16, NY ~ 13-21
    hour = datetime.utcnow().hour
    in_london = 7 <= hour <= 16
    in_ny = 13 <= hour <= 21
    return in_london or in_ny


def evaluate_fake_breakout(candle, next_candle, zone, direction):
    """
    direction: "SHORT" (fake breakout above supply) or "LONG" (fake breakdown below demand)
    zone: object with .top and .bottom
    """
    if zone is None or not _is_session_ok():
        return FakeBreakoutResult(False, 0, "NO_FAKE_BREAKOUT", "invalid zone/session")

    open_p = candle["open"]
    close_p = candle["close"]
    high_p = candle["high"]
    low_p = candle["low"]
    body = abs(close_p - open_p)
    range_p = high_p - low_p
    body_pct = body / range_p if range_p > 0 else 0

    score = 0
    reasons = []

    if direction == "SHORT":
        breaks_zone = high_p > zone.top and close_p >= zone.top
        closes_back_inside = close_p <= zone.top
    else:
        breaks_zone = low_p < zone.bottom and close_p <= zone.bottom
        closes_back_inside = close_p >= zone.bottom

    if breaks_zone:
        score += 2
    else:
        reasons.append("No breakout attempt")

    if closes_back_inside:
        score += 2
    else:
        reasons.append("True breakout")

    structure_fails = False
    if next_candle is not None:
        next_open = next_candle["open"]
        next_close = next_candle["close"]
        next_high = next_candle["high"]
        next_low = next_candle["low"]
        next_range = next_high - next_low
        next_body = abs(next_close - next_open)
        next_body_pct = next_body / next_range if next_range > 0 else 0
        if direction == "SHORT":
            structure_fails = (next_close <= next_open) or (next_high < high_p)
        else:
            structure_fails = (next_close >= next_open) or (next_low > low_p)
        if structure_fails:
            score += 2
        else:
            reasons.append("No follow-through")

        rejection_wick = False
        if direction == "SHORT":
            rejection_wick = (high_p - max(open_p, close_p)) >= 2 * body if body > 0 else False
        else:
            rejection_wick = (min(open_p, close_p) - low_p) >= 2 * body if body > 0 else False
        if rejection_wick:
            score += 1

        if next_body_pct < 0.3:
            score += 1
    else:
        reasons.append("No next candle")

    confirmed = score >= 5
    return FakeBreakoutResult(
        confirmed,
        score,
        "CONFIRMED_FAKE_BREAKOUT" if confirmed else "NO_FAKE_BREAKOUT",
        ", ".join(reasons),
    )
