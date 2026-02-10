"""
Expectancy Engine Module
Calculate trade expectancy based on historical win rate and R:R ratio
"""
from dataclasses import dataclass


@dataclass
class ExpectancyContext:
    """Context for expectancy calculation"""
    win_rate: float        # Historical win rate (e.g., 0.48 = 48%)
    rr: float              # Risk:Reward ratio
    min_expectancy: float = 0.10  # FUND minimum expectancy


@dataclass
class ExpectancyResult:
    """Result of expectancy evaluation"""
    expectancy: float
    valid: bool
    reason: str = ""


def evaluate_expectancy(ctx: ExpectancyContext) -> ExpectancyResult:
    """
    Evaluate trade expectancy based on win rate and R:R ratio.
    
    Formula: Expectancy = (Win Rate × R:R) - Loss Rate
    - Win Rate: Probability of winning (e.g., 0.48 = 48%)
    - R:R: Risk:Reward ratio (e.g., 1.5 = risk $1 to make $1.50)
    - Loss Rate: 1 - Win Rate
    
    Example:
        Win Rate = 48%, R:R = 1.5
        Expectancy = (0.48 × 1.5) - 0.52 = 0.72 - 0.52 = 0.20 ✅
        
        Win Rate = 40%, R:R = 1.0
        Expectancy = (0.40 × 1.0) - 0.60 = 0.40 - 0.60 = -0.20 ❌
    
    Args:
        ctx: ExpectancyContext with win_rate, rr, and min_expectancy
    
    Returns:
        ExpectancyResult with expectancy value and validation status
    """
    # Calculate loss rate
    loss_rate = 1 - ctx.win_rate
    
    # Calculate expectancy
    # Expectancy = (Win Rate × R:R) - Loss Rate
    expectancy = (ctx.win_rate * ctx.rr) - loss_rate
    
    # Validate against minimum
    valid = expectancy >= ctx.min_expectancy
    
    reason = "EXPECTANCY_OK" if valid else f"EXPECTANCY_TOO_LOW ({expectancy:.3f} < {ctx.min_expectancy})"
    
    return ExpectancyResult(
        expectancy=round(expectancy, 3),
        valid=valid,
        reason=reason
    )

