"""
Trade Exit Engine Module
Dynamic exit levels calculation based on market conditions
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExitContext:
    trade_mode: str
    entry_price: float
    atr: float
    direction: str
    market_regime: str = "BULL"
    trend_strength: str = "NORMAL"
    setup_type: str = "CONTINUATION"
    volatility_level: str = "NORMAL"


@dataclass
class ExitLevels:
    stop_loss: float
    tp1: float
    tp2: float
    tp3: Optional[float]
    tp1_percent: float
    tp2_percent: float
    tp3_percent: float
    trailing_enabled: bool
    trailing_distance: float
    breakeven_after_tp1: bool
    runner_enabled: bool
    max_hold_minutes: int
    reason: str
    tp_comment: str


def calculate_exit_levels(ctx: ExitContext) -> ExitLevels:
    """Calculate dynamic exit levels based on context"""
    # Base ATR multipliers
    sl_multiplier = 2.0
    tp1_multiplier = 2.5
    tp2_multiplier = 4.0
    tp3_multiplier = 6.0
    
    # Adjust based on trade mode
    if ctx.trade_mode == "SCALP_REBOUND":
        # Tighter stops for scalps
        sl_multiplier = 1.5
        tp1_multiplier = 1.8
        tp2_multiplier = 2.5
        tp3_multiplier = None  # No TP3 for scalps
        runner_enabled = False
        max_hold = 240  # 4 hours
    elif ctx.trade_mode == "TREND_FOLLOW":
        # Wider stops for trends
        sl_multiplier = 2.5
        tp1_multiplier = 3.0
        tp2_multiplier = 5.0
        tp3_multiplier = 8.0
        runner_enabled = True
        max_hold = 2880  # 48 hours
    else:
        # Standard mode
        runner_enabled = False
        max_hold = 1440  # 24 hours
    
    # Adjust based on volatility
    if ctx.volatility_level == "HIGH":
        sl_multiplier *= 1.3
        tp1_multiplier *= 1.2
        tp2_multiplier *= 1.2
        if tp3_multiplier:
            tp3_multiplier *= 1.2
    elif ctx.volatility_level == "LOW":
        sl_multiplier *= 0.8
        tp1_multiplier *= 0.9
        tp2_multiplier *= 0.9
        if tp3_multiplier:
            tp3_multiplier *= 0.9
    
    # Adjust based on trend strength
    if ctx.trend_strength == "STRONG":
        tp1_multiplier *= 1.1
        tp2_multiplier *= 1.15
        if tp3_multiplier:
            tp3_multiplier *= 1.2
    elif ctx.trend_strength == "WEAK":
        tp1_multiplier *= 0.9
        tp2_multiplier *= 0.9
    
    # Adjust based on market regime
    if ctx.market_regime in ["STRONG_BULL", "STRONG_BEAR"]:
        # Strong trends - wider targets
        tp2_multiplier *= 1.1
        if tp3_multiplier:
            tp3_multiplier *= 1.15
    elif ctx.market_regime == "RANGE":
        # Range markets - tighter targets
        tp1_multiplier *= 0.85
        tp2_multiplier *= 0.85
        tp3_multiplier = None  # No TP3 in range
    
    # Adjust based on setup type
    if ctx.setup_type == "BREAKOUT":
        tp1_multiplier *= 1.1
        tp2_multiplier *= 1.1
    
    # Calculate levels
    atr_value = ctx.atr
    
    if ctx.direction == "LONG":
        sl = ctx.entry_price - (atr_value * sl_multiplier)
        tp1 = ctx.entry_price + (atr_value * tp1_multiplier)
        tp2 = ctx.entry_price + (atr_value * tp2_multiplier)
        tp3 = ctx.entry_price + (atr_value * tp3_multiplier) if tp3_multiplier else None
        
        tp1_pct = ((tp1 - ctx.entry_price) / ctx.entry_price) * 100
        tp2_pct = ((tp2 - ctx.entry_price) / ctx.entry_price) * 100
        tp3_pct = ((tp3 - ctx.entry_price) / ctx.entry_price) * 100 if tp3 else 0
    else:  # SHORT
        sl = ctx.entry_price + (atr_value * sl_multiplier)
        tp1 = ctx.entry_price - (atr_value * tp1_multiplier)
        tp2 = ctx.entry_price - (atr_value * tp2_multiplier)
        tp3 = ctx.entry_price - (atr_value * tp3_multiplier) if tp3_multiplier else None
        
        tp1_pct = ((ctx.entry_price - tp1) / ctx.entry_price) * 100
        tp2_pct = ((ctx.entry_price - tp2) / ctx.entry_price) * 100
        tp3_pct = ((ctx.entry_price - tp3) / ctx.entry_price) * 100 if tp3 else 0
    
    # Generate comment
    mode_str = ctx.trade_mode.replace("_", " ").title()
    regime_str = ctx.market_regime.replace("_", " ").title()
    comment = f"{mode_str} | {regime_str} | {ctx.trend_strength} trend"
    
    if ctx.volatility_level != "NORMAL":
        comment += f" | {ctx.volatility_level} vol"
    
    return ExitLevels(
        stop_loss=sl,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        tp1_percent=tp1_pct,
        tp2_percent=tp2_pct,
        tp3_percent=tp3_pct,
        trailing_enabled=True,
        trailing_distance=1.5,
        breakeven_after_tp1=True,
        runner_enabled=runner_enabled,
        max_hold_minutes=max_hold,
        reason=f"{ctx.trade_mode}_{ctx.market_regime}",
        tp_comment=comment
    )
