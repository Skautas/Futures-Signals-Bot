def breakout_confirmed(close_price, zone_high, next_candle_bullish):
    if close_price > zone_high and next_candle_bullish:
        return True
    return False
