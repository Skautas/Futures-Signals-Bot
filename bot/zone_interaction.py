from enum import Enum


class ZoneInteraction(str, Enum):
    NONE = "NONE"
    TOUCH = "TOUCH"          # wick only
    INSIDE = "INSIDE"        # close inside zone
    BREAK_ARMED = "ARMED"    # close beyond zone
    CONFIRMED = "CONFIRMED"


def _get_candle_value(candle, key: str) -> float:
    if hasattr(candle, key):
        return float(getattr(candle, key))
    return float(candle[key])


def _is_bullish(candle) -> bool:
    return _get_candle_value(candle, "close") > _get_candle_value(candle, "open")


def _is_bearish(candle) -> bool:
    return _get_candle_value(candle, "close") < _get_candle_value(candle, "open")


def evaluate_zone_close(candle, zone, side: str) -> ZoneInteraction:
    """
    candle: open, high, low, close
    zone: top, bottom (+ optional atr)
    side: LONG / SHORT
    """
    zone_atr = getattr(zone, "atr", None)
    if zone_atr is None:
        zone_range = max(0.0, float(zone.top) - float(zone.bottom))
        zone_atr = zone_range
    buffer = zone_atr * 0.1  # adaptive buffer

    candle_high = _get_candle_value(candle, "high")
    candle_low = _get_candle_value(candle, "low")
    candle_close = _get_candle_value(candle, "close")

    if side == "LONG":
        if candle_high > zone.top and candle_close < zone.top:
            return ZoneInteraction.TOUCH
        if zone.bottom <= candle_close <= zone.top:
            return ZoneInteraction.INSIDE
        if candle_close > zone.top + buffer:
            return ZoneInteraction.BREAK_ARMED

    if side == "SHORT":
        if candle_low < zone.bottom and candle_close > zone.bottom:
            return ZoneInteraction.TOUCH
        if zone.bottom <= candle_close <= zone.top:
            return ZoneInteraction.INSIDE
        if candle_close < zone.bottom - buffer:
            return ZoneInteraction.BREAK_ARMED

    return ZoneInteraction.NONE


def confirm_follow_through(prev, curr, side: str) -> bool:
    if side == "LONG":
        return _get_candle_value(curr, "close") >= _get_candle_value(prev, "close") and not _is_bearish(curr)
    if side == "SHORT":
        return _get_candle_value(curr, "close") <= _get_candle_value(prev, "close") and not _is_bullish(curr)
    return False
