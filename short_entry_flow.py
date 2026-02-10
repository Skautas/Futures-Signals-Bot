from dataclasses import dataclass


@dataclass
class ShortMarketContext:
    symbol: str
    trend: str
    price: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    vwap: float
    rsi: float
    atr_pct: float
    last_candle: dict  # open, high, low, close


def price_near_resistance(ctx: ShortMarketContext, max_dist_pct=0.6):
    distances = [
        abs(ctx.price - ctx.ema_fast) / ctx.price * 100,
        abs(ctx.price - ctx.ema_mid) / ctx.price * 100,
        abs(ctx.price - ctx.vwap) / ctx.price * 100,
    ]
    return min(distances) <= max_dist_pct


def rsi_valid_for_short(ctx: ShortMarketContext):
    # NE oversold, NE overbought
    return 40 <= ctx.rsi <= 60


def bearish_rejection_candle(candle: dict):
    body = abs(candle["close"] - candle["open"])
    upper_wick = candle["high"] - max(candle["close"], candle["open"])
    return upper_wick > body * 1.2 and candle["close"] < candle["open"]


def evaluate_short_entry(ctx: ShortMarketContext):
    # 1️⃣ Trend filter
    if ctx.trend not in ["BEAR", "STRONG_BEAR"]:
        return False, "NOT_BEAR_TREND"

    # 2️⃣ Volatility sanity
    if ctx.atr_pct < 0.4:
        return False, "LOW_VOL"

    # 3️⃣ Location
    if not price_near_resistance(ctx):
        return False, "TOO_FAR_FROM_RESISTANCE"

    # 4️⃣ RSI zone
    if not rsi_valid_for_short(ctx):
        return False, "RSI_NOT_IDEAL_FOR_SHORT"

    # 5️⃣ Trigger candle
    if not bearish_rejection_candle(ctx.last_candle):
        return False, "NO_BEARISH_REJECTION"

    return True, "SHORT_ENTRY_OK"
