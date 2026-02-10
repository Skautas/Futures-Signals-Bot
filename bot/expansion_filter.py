def expansion_bias_block(
    direction: str,
    consecutive_candles: int,
    pullback_depth_pct: float,
    atr_pct: float
) -> bool:
    if direction == "SHORT":
        if consecutive_candles >= 3 and pullback_depth_pct < 25 and atr_pct > 1.1:
            return True

    if direction == "LONG":
        if consecutive_candles >= 3 and pullback_depth_pct < 25 and atr_pct > 1.1:
            return True

    return False
