"""
Market Regime Engine Module
Market regime detection (BULL/BEAR/RANGE)
"""
from dataclasses import dataclass


@dataclass
class RegimeContext:
    ema_fast: float
    ema_slow: float
    ema_slope: float
    atr_ratio: float
    rsi: float
    adx: float
    vwap_distance: float


@dataclass
class RegimeResult:
    regime: str  # "BULL", "BEAR", "RANGE"
    confidence: float
    reason: str


def detect_market_regime(ctx: RegimeContext) -> RegimeResult:
    """Detect market regime from context"""
    # Multi-factor regime detection
    
    # Factor 1: EMA relationship
    ema_bullish = ctx.ema_fast > ctx.ema_slow
    ema_bearish = ctx.ema_fast < ctx.ema_slow
    ema_neutral = abs(ctx.ema_fast - ctx.ema_slow) / ctx.ema_slow < 0.01  # Within 1%
    
    # Factor 2: EMA slope (momentum)
    slope_bullish = ctx.ema_slope > 0.0001  # Positive slope
    slope_bearish = ctx.ema_slope < -0.0001  # Negative slope
    
    # Factor 3: RSI position
    rsi_bullish = ctx.rsi > 50
    rsi_bearish = ctx.rsi < 50
    rsi_extreme_bull = ctx.rsi > 60
    rsi_extreme_bear = ctx.rsi < 40
    
    # Factor 4: ADX (trend strength)
    strong_trend = ctx.adx > 25
    weak_trend = ctx.adx < 20
    
    # Factor 5: Volatility (ATR ratio)
    high_vol = ctx.atr_ratio > 1.2
    low_vol = ctx.atr_ratio < 0.8
    
    # Factor 6: VWAP distance
    vwap_bullish = ctx.vwap_distance > 0.002  # Price above VWAP
    vwap_bearish = ctx.vwap_distance < -0.002  # Price below VWAP
    
    # Score each regime
    bull_score = 0
    bear_score = 0
    range_score = 0
    
    # BULL scoring
    if ema_bullish:
        bull_score += 2
    if slope_bullish:
        bull_score += 1
    if rsi_bullish:
        bull_score += 1
    if rsi_extreme_bull:
        bull_score += 1
    if strong_trend:
        bull_score += 1
    if vwap_bullish:
        bull_score += 1
    
    # BEAR scoring
    if ema_bearish:
        bear_score += 2
    if slope_bearish:
        bear_score += 1
    if rsi_bearish:
        bear_score += 1
    if rsi_extreme_bear:
        bear_score += 1
    if strong_trend:
        bear_score += 1
    if vwap_bearish:
        bear_score += 1
    
    # RANGE scoring (consolidation)
    if ema_neutral:
        range_score += 2
    if weak_trend:
        range_score += 2
    if not rsi_extreme_bull and not rsi_extreme_bear:
        range_score += 1
    if abs(ctx.vwap_distance) < 0.001:
        range_score += 1
    
    # Determine regime
    max_score = max(bull_score, bear_score, range_score)
    
    if max_score == bull_score and bull_score >= 4:
        regime = "BULL"
        confidence = min(90.0, 50.0 + (bull_score * 8))
        reason = f"Strong bullish signals (score: {bull_score})"
    elif max_score == bear_score and bear_score >= 4:
        regime = "BEAR"
        confidence = min(90.0, 50.0 + (bear_score * 8))
        reason = f"Strong bearish signals (score: {bear_score})"
    elif max_score == range_score and range_score >= 3:
        regime = "RANGE"
        confidence = min(80.0, 40.0 + (range_score * 10))
        reason = f"Range-bound market (score: {range_score})"
    else:
        # Weak signals - default based on EMA
        if ema_bullish:
            regime = "BULL"
            confidence = 55.0
            reason = "Weak bullish (EMA only)"
        elif ema_bearish:
            regime = "BEAR"
            confidence = 55.0
            reason = "Weak bearish (EMA only)"
        else:
            regime = "RANGE"
            confidence = 50.0
            reason = "Unclear signals"
    
    return RegimeResult(
        regime=regime,
        confidence=confidence,
        reason=reason
    )
