from typing import List, Dict, Optional


def detect_swings(candles: List[Dict], lookback: int = 2) -> List[Dict]:
    swings = []
    if not candles or len(candles) < (lookback * 2 + 1):
        return swings

    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]

        is_swing_high = all(
            high > candles[i - j]["high"] for j in range(1, lookback + 1)
        ) and all(
            high > candles[i + j]["high"] for j in range(1, lookback + 1)
        )
        if is_swing_high:
            swings.append({"type": "HIGH", "price": high, "index": i})

        is_swing_low = all(
            low < candles[i - j]["low"] for j in range(1, lookback + 1)
        ) and all(
            low < candles[i + j]["low"] for j in range(1, lookback + 1)
        )
        if is_swing_low:
            swings.append({"type": "LOW", "price": low, "index": i})

    return swings


def detect_bos(
    last_close: float,
    last_swing_high: Optional[float],
    last_swing_low: Optional[float],
    atr: float,
) -> Optional[str]:
    if last_swing_high is None or last_swing_low is None or atr is None:
        return None
    buffer = 0.15 * atr

    if last_close > last_swing_high + buffer:
        return "BULLISH"

    if last_close < last_swing_low - buffer:
        return "BEARISH"

    return None


def detect_choch(prev_trend: Optional[str], swings: List[Dict]) -> Optional[str]:
    if prev_trend == "BULLISH":
        last_highs = [s for s in swings if s["type"] == "HIGH"]
        last_lows = [s for s in swings if s["type"] == "LOW"]

        if len(last_lows) >= 2 and last_lows[-1]["price"] < last_lows[-2]["price"]:
            return "BEARISH_WARNING"

    if prev_trend == "BEARISH":
        last_highs = [s for s in swings if s["type"] == "HIGH"]

        if len(last_highs) >= 2 and last_highs[-1]["price"] > last_highs[-2]["price"]:
            return "BULLISH_WARNING"

    return None


def update_htf_structure(htf_candles: List[Dict], atr: float, direction_lock) -> Dict:
    swings = detect_swings(htf_candles)
    if not htf_candles:
        return {"bos": None, "lock": direction_lock.lock, "choch": None}

    last_close = htf_candles[-1]["close"]
    last_swing_high = None
    last_swing_low = None
    highs = [s["price"] for s in swings if s["type"] == "HIGH"]
    lows = [s["price"] for s in swings if s["type"] == "LOW"]
    if highs:
        last_swing_high = max(highs)
    if lows:
        last_swing_low = min(lows)

    bos = detect_bos(last_close, last_swing_high, last_swing_low, atr)
    direction_lock.update(bos)
    choch = detect_choch(direction_lock.lock, swings)

    return {
        "bos": bos,
        "lock": direction_lock.lock,
        "choch": choch,
    }


def structure_hold(candles: List[Dict], direction: str) -> bool:
    swings = detect_swings(candles)
    if not candles or len(swings) < 2:
        return False
    last_close = candles[-1]["close"]
    swing_lows = [s for s in swings if s["type"] == "LOW"]
    swing_highs = [s for s in swings if s["type"] == "HIGH"]

    if direction == "LONG" and len(swing_lows) >= 2:
        prev_low = swing_lows[-2]["price"]
        last_low = swing_lows[-1]["price"]
        higher_low = last_low > prev_low
        not_broken = last_close > last_low
        return higher_low and not_broken

    if direction == "SHORT" and len(swing_highs) >= 2:
        prev_high = swing_highs[-2]["price"]
        last_high = swing_highs[-1]["price"]
        lower_high = last_high < prev_high
        not_broken = last_close < last_high
        return lower_high and not_broken

    return False


def lower_low_printed(candles: List[Dict]) -> bool:
    swings = detect_swings(candles)
    swing_lows = [s for s in swings if s["type"] == "LOW"]
    if len(swing_lows) < 2:
        return False
    return swing_lows[-1]["price"] < swing_lows[-2]["price"]
