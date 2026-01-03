"""
Entry Optimizer 5m Module
Optimize entry timing on 5-minute timeframe
"""
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np


@dataclass
class EntryOptimizationResult:
    allow_entry: bool
    wait_reason: Optional[str]
    score_boost: int
    optimized_price: Optional[float]


def optimize_entry_5m(
    df_5m: pd.DataFrame,
    direction: str,
    ideal_entry_price: float,
    base_rr: float
) -> EntryOptimizationResult:
    """Optimize entry on 5m timeframe. Returns optimization result"""
    if df_5m is None or len(df_5m) < 5:
        return EntryOptimizationResult(
            allow_entry=True,
            wait_reason=None,
            score_boost=0,
            optimized_price=ideal_entry_price
        )
    
    try:
        # Calculate indicators on 5m
        closes = df_5m['close'].values
        highs = df_5m['high'].values
        lows = df_5m['low'].values
        
        current_price = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        
        # RSI on 5m
        rsi_period = 14
        if len(closes) >= rsi_period:
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains[-rsi_period:]) if len(gains) >= rsi_period else 0
            avg_loss = np.mean(losses[-rsi_period:]) if len(losses) >= rsi_period else 0
            
            if avg_loss == 0:
                rsi_5m = 100
            else:
                rs = avg_gain / avg_loss
                rsi_5m = 100 - (100 / (1 + rs))
        else:
            rsi_5m = 50
        
        # EMA on 5m
        ema_period = 9
        if len(closes) >= ema_period:
            ema_5m = pd.Series(closes).ewm(span=ema_period).mean().iloc[-1]
        else:
            ema_5m = current_price
        
        # ATR on 5m
        if len(df_5m) >= 14:
            high_low = highs - lows
            high_close = np.abs(highs - np.roll(closes, 1))
            low_close = np.abs(lows - np.roll(closes, 1))
            tr = np.maximum(high_low, np.maximum(high_close, low_close))
            atr_5m = np.mean(tr[-14:])
        else:
            atr_5m = (current_high - current_low) * 0.5
        
        # Price action analysis
        recent_candles = df_5m.tail(3)
        is_consolidating = (recent_candles['high'].max() - recent_candles['low'].min()) < (atr_5m * 1.5)
        
        score_boost = 0
        wait_reason = None
        optimized_price = ideal_entry_price
        
        # LONG optimization
        if direction == "LONG":
            # Check if price is near support (EMA or recent low)
            price_to_ema = abs(current_price - ema_5m) / current_price
            price_to_low = abs(current_price - current_low) / current_price
            
            # Good entry conditions
            if rsi_5m < 40 and current_price < ema_5m:
                # Oversold on 5m, good for long entry
                score_boost += 5
                optimized_price = min(ideal_entry_price, current_low * 1.001)  # Slightly above low
            elif rsi_5m < 50 and price_to_ema < 0.002:  # Within 0.2% of EMA
                score_boost += 3
                optimized_price = min(ideal_entry_price, ema_5m * 1.0005)
            elif is_consolidating and current_price <= recent_candles['low'].min() * 1.002:
                # Near consolidation low
                score_boost += 2
                optimized_price = min(ideal_entry_price, recent_candles['low'].min() * 1.001)
            
            # Bad entry conditions - suggest waiting
            if rsi_5m > 70:
                wait_reason = "RSI_5M_OVERBOUGHT"
            elif current_price > ema_5m * 1.01:  # Too far above EMA
                wait_reason = "PRICE_TOO_HIGH_5M"
        
        # SHORT optimization
        elif direction == "SHORT":
            # Check if price is near resistance (EMA or recent high)
            price_to_ema = abs(current_price - ema_5m) / current_price
            price_to_high = abs(current_price - current_high) / current_price
            
            # Good entry conditions
            if rsi_5m > 60 and current_price > ema_5m:
                # Overbought on 5m, good for short entry
                score_boost += 5
                optimized_price = max(ideal_entry_price, current_high * 0.999)  # Slightly below high
            elif rsi_5m > 50 and price_to_ema < 0.002:  # Within 0.2% of EMA
                score_boost += 3
                optimized_price = max(ideal_entry_price, ema_5m * 0.9995)
            elif is_consolidating and current_price >= recent_candles['high'].max() * 0.998:
                # Near consolidation high
                score_boost += 2
                optimized_price = max(ideal_entry_price, recent_candles['high'].max() * 0.999)
            
            # Bad entry conditions - suggest waiting
            if rsi_5m < 30:
                wait_reason = "RSI_5M_OVERSOLD"
            elif current_price < ema_5m * 0.99:  # Too far below EMA
                wait_reason = "PRICE_TOO_LOW_5M"
        
        # Always allow entry (5m is suggestion, not blocker)
        # But provide feedback
        return EntryOptimizationResult(
            allow_entry=True,  # Never block, only suggest
            wait_reason=wait_reason,
            score_boost=score_boost,
            optimized_price=optimized_price
        )
    
    except Exception as e:
        print(f"5m optimizer error: {e}")
        return EntryOptimizationResult(
            allow_entry=True,
            wait_reason=None,
            score_boost=0,
            optimized_price=ideal_entry_price
        )
