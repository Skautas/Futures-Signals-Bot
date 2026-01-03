"""
Signal Density Engine Module
Adaptive signal filtering based on market density
"""
from dataclasses import dataclass


@dataclass
class DensityContext:
    base_score: float
    trend_strength: str
    market_regime: str
    volatility_spike: bool
    consolidation: bool
    session_liquid: bool
    trade_mode: str


@dataclass
class DensityResult:
    allowed: bool
    final_score: float
    min_score_required: float
    reason: str


def evaluate_signal_density(ctx: DensityContext) -> DensityResult:
    """Evaluate signal density and adjust thresholds"""
    # Base minimum score
    base_min_score = 60.0
    min_score = base_min_score
    score_adjustment = 0.0
    
    # Adjust based on market regime
    if ctx.market_regime in ["STRONG_BULL", "STRONG_BEAR"]:
        # Strong trends - can be more lenient
        min_score -= 5
        score_adjustment += 3
    elif ctx.market_regime == "RANGE":
        # Range markets - need higher quality
        min_score += 5
        score_adjustment -= 2
    
    # Adjust based on trend strength
    if ctx.trend_strength == "STRONG":
        min_score -= 3
        score_adjustment += 2
    elif ctx.trend_strength == "WEAK":
        min_score += 3
        score_adjustment -= 2
    
    # Adjust based on volatility
    if ctx.volatility_spike:
        # High volatility - be more selective
        min_score += 8
        score_adjustment -= 5
    elif not ctx.volatility_spike and ctx.consolidation:
        # Low volatility consolidation - can be more lenient
        min_score -= 3
        score_adjustment += 2
    
    # Adjust based on session liquidity
    if ctx.session_liquid:
        # Liquid session - better execution, can be more lenient
        min_score -= 2
        score_adjustment += 1
    else:
        # Illiquid session - need higher quality
        min_score += 3
        score_adjustment -= 1
    
    # Adjust based on trade mode
    if ctx.trade_mode == "SCALP_REBOUND":
        # Rebound trades need higher quality
        min_score += 5
    elif ctx.trade_mode == "TREND_FOLLOW":
        # Trend following can be more lenient in strong trends
        if ctx.trend_strength == "STRONG":
            min_score -= 3
    
    # Calculate final score
    final_score = ctx.base_score + score_adjustment
    
    # Determine if allowed
    allowed = final_score >= min_score
    
    # Generate reason
    if allowed:
        reason = f"DENSITY_OK (score: {final_score:.1f} >= {min_score:.1f})"
    else:
        reason = f"DENSITY_BLOCKED (score: {final_score:.1f} < {min_score:.1f})"
    
    return DensityResult(
        allowed=allowed,
        final_score=final_score,
        min_score_required=min_score,
        reason=reason
    )
