from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class CashflowContext:
    symbol: str
    trend_1h: str              # "BULL" | "BEAR"
    regime: str                # "TREND" | "RANGE" | "CHAOTIC"
    rsi_5m: float
    rsi_15m: float
    price_above_vwap: bool
    ema_stack_ok: bool         # 9 > 21 > 50 for long
    atr_pct: float             # current ATR %
    rr_after_fees: float
    last_trade_time: datetime | None


MIN_RR = 0.30
MAX_RSI = 80
MIN_ATR_PCT = 0.15
MAX_ATR_PCT = 2.5
MIN_HOURS_BETWEEN_TRADES = 20


def evaluate_cashflow_entry(ctx: CashflowContext):
    """
    Returns:
        (bool, reason)
    """

    # 0️⃣ Trend check
    if ctx.trend_1h not in ("BULL", "BEAR"):
        return False, "NO_TREND"

    # 1️⃣ Regime filter
    if ctx.regime != "TREND":
        return False, "BAD_REGIME"

    # 2️⃣ Time spacing (no overtrading)
    if ctx.last_trade_time:
        delta = datetime.utcnow() - ctx.last_trade_time
        if delta < timedelta(hours=MIN_HOURS_BETWEEN_TRADES):
            return False, "TOO_SOON"

    # 3️⃣ Volatility sanity check
    if ctx.atr_pct < MIN_ATR_PCT:
        return False, "LOW_VOL"
    if ctx.atr_pct > MAX_ATR_PCT:
        return False, "EXTREME_VOL"

    # 4️⃣ Directional logic
    if ctx.trend_1h == "BULL":
        if ctx.rsi_5m > MAX_RSI:
            return False, "FOMO_RSI"
        if not ctx.price_above_vwap:
            return False, "BELOW_VWAP"
        if not ctx.ema_stack_ok:
            return False, "EMA_NOT_ALIGNED"

    if ctx.trend_1h == "BEAR":
        if ctx.rsi_5m < (100 - MAX_RSI):
            return False, "FOMO_RSI"
        if ctx.price_above_vwap:
            return False, "ABOVE_VWAP"
        if not ctx.ema_stack_ok:
            return False, "EMA_NOT_ALIGNED"

    # 5️⃣ RR filter (cashflow relaxed)
    if ctx.rr_after_fees < MIN_RR:
        return False, "RR_TOO_LOW"

    return True, "CASHFLOW_ENTRY_OK"
