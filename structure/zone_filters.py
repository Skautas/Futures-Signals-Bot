from typing import List, Dict, Optional


def _candles_from_df(df) -> List[Dict]:
    if df is None or len(df) == 0:
        return []
    return df[["open", "high", "low", "close"]].to_dict("records")


def detect_demand_zones(candles: List[Dict], atr: float, impulse_mult: float = 1.5) -> List[Dict]:
    zones = []
    if not candles or atr is None:
        return zones
    for i in range(len(candles) - 3):
        c = candles[i]
        if c["close"] < c["open"]:
            impulse = candles[i + 1]["high"] - candles[i]["low"]
            if impulse > impulse_mult * atr:
                zones.append({
                    "type": "DEMAND",
                    "top": c["open"],
                    "bottom": c["low"],
                    "index": i,
                    "strength": impulse / atr
                })
    return zones


def detect_supply_zones(candles: List[Dict], atr: float, impulse_mult: float = 1.5) -> List[Dict]:
    zones = []
    if not candles or atr is None:
        return zones
    for i in range(len(candles) - 3):
        c = candles[i]
        if c["close"] > c["open"]:
            impulse = candles[i]["high"] - candles[i + 1]["low"]
            if impulse > impulse_mult * atr:
                zones.append({
                    "type": "SUPPLY",
                    "top": c["high"],
                    "bottom": c["open"],
                    "index": i,
                    "strength": impulse / atr
                })
    return zones


def detect_equal_highs(candles: List[Dict], tolerance: float) -> List[Dict]:
    levels = []
    if not candles:
        return levels
    for i in range(len(candles) - 1):
        if abs(candles[i]["high"] - candles[i + 1]["high"]) <= tolerance:
            levels.append({
                "type": "LIQUIDITY_HIGH",
                "price": candles[i]["high"]
            })
    return levels


def detect_equal_lows(candles: List[Dict], tolerance: float) -> List[Dict]:
    levels = []
    if not candles:
        return levels
    for i in range(len(candles) - 1):
        if abs(candles[i]["low"] - candles[i + 1]["low"]) <= tolerance:
            levels.append({
                "type": "LIQUIDITY_LOW",
                "price": candles[i]["low"]
            })
    return levels


def _count_zone_tests(candles: List[Dict], zone: Dict) -> int:
    tests = 0
    top = zone["top"]
    bottom = zone["bottom"]
    for c in candles[zone["index"] + 1:]:
        if c["high"] >= bottom and c["low"] <= top:
            tests += 1
    return tests


def _zone_broken(candles: List[Dict], zone: Dict, buffer: float) -> bool:
    top = zone["top"]
    bottom = zone["bottom"]
    for c in candles[zone["index"] + 1:]:
        close = c["close"]
        if zone["type"] == "DEMAND" and close < bottom - buffer:
            return True
        if zone["type"] == "SUPPLY" and close > top + buffer:
            return True
    return False


def filter_zones(
    zones: List[Dict],
    candles: List[Dict],
    buffer_atr: float,
    max_age: int,
    max_tests: int,
    atr: float,
) -> List[Dict]:
    filtered = []
    if not candles or atr is None:
        return filtered
    buffer = buffer_atr * atr
    for z in zones:
        age = (len(candles) - 1) - z["index"]
        if age > max_age:
            continue
        if _zone_broken(candles, z, buffer):
            continue
        if _count_zone_tests(candles, z) >= max_tests:
            continue
        filtered.append(z)
    return filtered


def merge_zones(zones_a: List[Dict], zones_b: List[Dict]) -> List[Dict]:
    merged = []
    for z in zones_a:
        z = dict(z)
        z["timeframe"] = "HTF"
        merged.append(z)
    for z in zones_b:
        z = dict(z)
        z["timeframe"] = "LTF"
        merged.append(z)
    # Mark overlap as strong
    for i in range(len(merged)):
        for j in range(i + 1, len(merged)):
            a = merged[i]
            b = merged[j]
            if a["type"] != b["type"]:
                continue
            if max(a["bottom"], b["bottom"]) <= min(a["top"], b["top"]):
                a["strong"] = True
                b["strong"] = True
    return merged


def build_zones(
    df_4h,
    df_1h,
    atr_4h: float,
    atr_1h: float,
    impulse_mult: float = 1.5,
    buffer_atr: float = 0.15,
    max_age: int = 150,
    max_tests: int = 3,
) -> Dict:
    candles_4h = _candles_from_df(df_4h)
    candles_1h = _candles_from_df(df_1h)

    zones_4h = filter_zones(
        detect_demand_zones(candles_4h, atr_4h, impulse_mult)
        + detect_supply_zones(candles_4h, atr_4h, impulse_mult),
        candles_4h,
        buffer_atr,
        max_age,
        max_tests,
        atr_4h,
    )
    zones_1h = filter_zones(
        detect_demand_zones(candles_1h, atr_1h, impulse_mult)
        + detect_supply_zones(candles_1h, atr_1h, impulse_mult),
        candles_1h,
        buffer_atr,
        max_age,
        max_tests,
        atr_1h,
    )
    zones = merge_zones(zones_4h, zones_1h)

    return {
        "zones": zones,
        "liquidity_highs": detect_equal_highs(candles_1h, tolerance=0.1 * atr_1h),
        "liquidity_lows": detect_equal_lows(candles_1h, tolerance=0.1 * atr_1h),
    }


def zone_filter(
    zones: List[Dict],
    direction: str,
    price: float,
    last_close: float,
    atr: float,
    buffer_atr: float = 0.15,
) -> Dict:
    buffer = buffer_atr * atr
    if direction == "LONG":
        for z in zones:
            if z["type"] != "SUPPLY":
                continue
            if z["bottom"] <= price <= z["top"]:
                if last_close <= z["top"] + buffer:
                    return {"blocked": True, "reason": "SUPPLY_ZONE_BLOCK", "zone": z}
        return {"blocked": False}
    if direction == "SHORT":
        for z in zones:
            if z["type"] != "DEMAND":
                continue
            if z["bottom"] <= price <= z["top"]:
                if last_close >= z["bottom"] - buffer:
                    return {"blocked": True, "reason": "DEMAND_ZONE_BLOCK", "zone": z}
        return {"blocked": False}
    return {"blocked": False}
