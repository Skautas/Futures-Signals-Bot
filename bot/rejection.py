from dataclasses import dataclass


@dataclass
class RejectionContext:
    candle_open: float
    candle_close: float
    candle_high: float
    candle_low: float
    zone_high: float
    zone_low: float
    atr: float
    volume_spike: bool
    prev_candle_close: float
    momentum_loss: bool
    htf_trend: str


@dataclass
class RejectionResult:
    confirmed: bool
    score: int
    reason: str
    details: str = ""


REJECTION_THRESHOLDS = {
    "CASHFLOW": 4,
    "SWING": 6,
}


def _flip_context(ctx: RejectionContext) -> RejectionContext:
    return RejectionContext(
        candle_open=-ctx.candle_open,
        candle_close=-ctx.candle_close,
        candle_high=-ctx.candle_low,
        candle_low=-ctx.candle_high,
        zone_high=-ctx.zone_low,
        zone_low=-ctx.zone_high,
        atr=ctx.atr,
        volume_spike=ctx.volume_spike,
        prev_candle_close=-ctx.prev_candle_close,
        momentum_loss=ctx.momentum_loss,
        htf_trend=ctx.htf_trend,
    )


def wick_dominance(ctx: RejectionContext) -> int:
    body = abs(ctx.candle_close - ctx.candle_open)
    upper_wick = ctx.candle_high - max(ctx.candle_close, ctx.candle_open)
    if upper_wick >= body * 1.5:
        return 2
    if upper_wick >= body:
        return 1
    return 0


def close_location(ctx: RejectionContext) -> int:
    zone_mid = (ctx.zone_high + ctx.zone_low) / 2
    if ctx.candle_close < zone_mid:
        return 2
    if ctx.candle_close < ctx.zone_high:
        return 1
    return 0


def zone_respect(ctx: RejectionContext) -> int:
    if ctx.candle_close < ctx.zone_high and ctx.prev_candle_close < ctx.zone_high:
        return 2
    if ctx.candle_close < ctx.zone_high:
        return 1
    return 0


def momentum_score(ctx: RejectionContext) -> int:
    return 2 if ctx.momentum_loss else 0


def volume_score(ctx: RejectionContext) -> int:
    if ctx.volume_spike:
        return 1
    return 0


def htf_alignment(ctx: RejectionContext, direction: str) -> int:
    if direction == "SHORT" and ctx.htf_trend == "STRONG_BEAR":
        return 1
    if direction == "LONG" and ctx.htf_trend == "STRONG_BULL":
        return 1
    return 0


def calculate_rejection_score(ctx: RejectionContext, direction: str) -> int:
    score = 0
    score += wick_dominance(ctx)
    score += close_location(ctx)
    score += zone_respect(ctx)
    score += momentum_score(ctx)
    score += volume_score(ctx)
    score += htf_alignment(ctx, direction)
    return score


def evaluate_rejection(candle, prev_candle, next_candle, context, rsi_series=None) -> RejectionResult:
    """
    context keys:
      - direction: "LONG" | "SHORT"
      - location_valid: bool
      - zone_high: float | None
      - zone_low: float | None
      - atr: float | None
      - volume_spike: bool
      - prev_candle_close: float | None
      - momentum_loss: bool
      - htf_trend: str
      - mode: "CASHFLOW" | "SWING"
    """
    if not context.get("location_valid"):
        return RejectionResult(False, 0, "NO_REJECTION", "location invalid")

    direction = context.get("direction")
    if direction not in ("LONG", "SHORT"):
        return RejectionResult(False, 0, "NO_REJECTION", "invalid direction")

    zone_high = context.get("zone_high")
    zone_low = context.get("zone_low")
    if zone_high is None or zone_low is None:
        return RejectionResult(False, 0, "NO_REJECTION", "zone missing")

    atr = context.get("atr") or 0.0
    prev_close = context.get("prev_candle_close")
    if prev_close is None:
        return RejectionResult(False, 0, "NO_REJECTION", "prev close missing")

    rejection_ctx = RejectionContext(
        candle_open=candle["open"],
        candle_close=candle["close"],
        candle_high=candle["high"],
        candle_low=candle["low"],
        zone_high=zone_high,
        zone_low=zone_low,
        atr=atr,
        volume_spike=context.get("volume_spike", False),
        prev_candle_close=prev_close,
        momentum_loss=context.get("momentum_loss", False),
        htf_trend=context.get("htf_trend", "RANGE"),
    )

    scoring_ctx = rejection_ctx if direction == "SHORT" else _flip_context(rejection_ctx)
    score = calculate_rejection_score(scoring_ctx, direction)

    details = (
        f"REJECTION_SCORE={score} | "
        f"wick={wick_dominance(scoring_ctx)} "
        f"close={close_location(scoring_ctx)} "
        f"zone={zone_respect(scoring_ctx)} "
        f"momentum={momentum_score(scoring_ctx)} "
        f"volume={volume_score(scoring_ctx)} "
        f"htf={htf_alignment(scoring_ctx, direction)}"
    )

    mode = context.get("mode", "CASHFLOW")
    threshold = REJECTION_THRESHOLDS.get(mode, REJECTION_THRESHOLDS["CASHFLOW"])
    confirmed = score >= threshold
    reason = "CONFIRMED_REJECTION" if confirmed else "NO_ENTRY"
    return RejectionResult(confirmed, score, reason, details)
