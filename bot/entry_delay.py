from dataclasses import dataclass


@dataclass
class EntryDelayResult:
    state: str
    reason: str


def evaluate_entry_delay(trigger_candle, confirm_candle, direction):
    if trigger_candle is None or confirm_candle is None:
        return EntryDelayResult("WAITING_CONFIRMATION", "Missing candles")

    trig_low = trigger_candle["low"]
    trig_high = trigger_candle["high"]
    confirm_open = confirm_candle["open"]
    confirm_close = confirm_candle["close"]
    confirm_high = confirm_candle["high"]
    confirm_low = confirm_candle["low"]

    confirm_range = confirm_high - confirm_low
    confirm_body = abs(confirm_close - confirm_open)
    confirm_body_pct = (confirm_body / confirm_range) if confirm_range > 0 else 0

    is_bearish = confirm_close < confirm_open
    is_bullish = confirm_close > confirm_open

    if direction == "SHORT":
        if confirm_close < trig_low:
            return EntryDelayResult("CONFIRMED", "Close below trigger low")
        if is_bearish and confirm_body_pct >= 0.5:
            return EntryDelayResult("CONFIRMED", "Bearish body >= 50%")
        if confirm_high > trig_high:
            return EntryDelayResult("CANCELLED", "New high after rejection")
        return EntryDelayResult("WAITING_CONFIRMATION", "No confirmation yet")

    if direction == "LONG":
        if confirm_close > trig_high:
            return EntryDelayResult("CONFIRMED", "Close above trigger high")
        if is_bullish and confirm_body_pct >= 0.5:
            return EntryDelayResult("CONFIRMED", "Bullish body >= 50%")
        if confirm_low < trig_low:
            return EntryDelayResult("CANCELLED", "New low after rejection")
        return EntryDelayResult("WAITING_CONFIRMATION", "No confirmation yet")

    return EntryDelayResult("WAITING_CONFIRMATION", "Invalid direction")
