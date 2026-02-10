"""
Quantitative Analytics Module
Mathematical analysis using Monte Carlo, ARIMA, Mean Reversion, Fibonacci
"""
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scipy import stats
from scipy.optimize import minimize
import warnings
warnings.filterwarnings('ignore')


class QuantAnalytics:
    """Quantitative analysis engine for crypto assets"""
    
    def __init__(self):
        self.results = {}
        self.correlation_matrix = None
    
    def run_all_assets(self) -> Tuple[Dict, Any]:
        """Run analysis for all assets. Returns (results, correlation)"""
        results = {}
        
        # This would normally fetch data for all assets
        # For now, return empty dict - will be populated by main bot
        return results, self.correlation_matrix
    
    def get_quant_signal_bias(self, data: Dict) -> Tuple[int, List[str]]:
        """Get signal bias from quant analysis. Returns (bias_score, signals_list)"""
        if not data or not isinstance(data, dict):
            return 0, []
        
        signals = []
        bias_score = 0
        
        # Monte Carlo Analysis
        mc_bias = data.get('monte_carlo_bias', 0)
        if abs(mc_bias) > 5:
            bias_score += int(mc_bias)
            signals.append(f"MC_{'BULL' if mc_bias > 0 else 'BEAR'}")
        
        # Mean Reversion Signal
        mean_reversion = data.get('mean_reversion_signal', 0)
        if abs(mean_reversion) > 0.3:
            bias_score += int(mean_reversion * 10)
            signals.append(f"MR_{'OVERSOLD' if mean_reversion < 0 else 'OVERBOUGHT'}")
        
        # ARIMA Forecast
        arima_trend = data.get('arima_trend', 0)
        if abs(arima_trend) > 0.2:
            bias_score += int(arima_trend * 15)
            signals.append(f"ARIMA_{'UP' if arima_trend > 0 else 'DOWN'}")
        
        # Fibonacci Levels
        fib_signal = data.get('fibonacci_signal', 0)
        if abs(fib_signal) > 0.2:
            bias_score += int(fib_signal * 10)
            signals.append("FIB_ZONE")
        
        # Volatility Analysis
        vol_regime = data.get('volatility_regime', 'NORMAL')
        if vol_regime == 'HIGH':
            bias_score -= 5  # Reduce confidence in high volatility
            signals.append("HIGH_VOL")
        elif vol_regime == 'LOW':
            bias_score += 3
            signals.append("LOW_VOL")
        
        # Limit bias to reasonable range
        bias_score = max(-30, min(30, bias_score))
        
        return bias_score, signals
    
    def monte_carlo_simulation(self, prices: pd.Series, n_simulations: int = 1000, days: int = 5) -> Dict:
        """Monte Carlo simulation for price prediction"""
        if len(prices) < 20:
            return {'bias': 0, 'confidence': 0, 'prob_up': 0.5, 'prob_down': 0.5, 'expected_price': 0, 'current_price': 0, 'prob_up_10%': 0, 'prob_down_10%': 0}
        
        returns = prices.pct_change().dropna()
        if len(returns) < 10:
            return {'bias': 0, 'confidence': 0, 'prob_up': 0.5, 'prob_down': 0.5, 'expected_price': 0, 'current_price': 0, 'prob_up_10%': 0, 'prob_down_10%': 0}
        
        mean_return = returns.mean()
        std_return = returns.std()
        
        # Simulate future paths
        simulations = []
        for _ in range(n_simulations):
            future_returns = np.random.normal(mean_return, std_return, days)
            future_price = prices.iloc[-1] * (1 + future_returns).prod()
            simulations.append(future_price)
        
        current_price = prices.iloc[-1]
        avg_future = np.mean(simulations)
        
        # Calculate bias (positive = bullish, negative = bearish)
        bias_pct = ((avg_future - current_price) / current_price) * 100
        confidence = min(1.0, abs(bias_pct) / 5.0)
        
        # Probabilities for up/down and +/-10% moves
        up_count = sum(1 for p in simulations if p > current_price)
        down_count = sum(1 for p in simulations if p < current_price)
        prob_up = up_count / len(simulations) if simulations else 0.5
        prob_down = down_count / len(simulations) if simulations else 0.5
        
        up_10 = current_price * 1.10
        down_10 = current_price * 0.90
        prob_up_10 = sum(1 for p in simulations if p >= up_10) / len(simulations) if simulations else 0
        prob_down_10 = sum(1 for p in simulations if p <= down_10) / len(simulations) if simulations else 0
        
        return {
            'bias': bias_pct,
            'confidence': confidence,
            'expected_price': avg_future,
            'current_price': current_price,
            'prob_up': prob_up,
            'prob_down': prob_down,
            'prob_up_10%': prob_up_10,
            'prob_down_10%': prob_down_10
        }
    
    def arima_forecast(self, prices: pd.Series, periods: int = 5) -> Dict:
        """Simple ARIMA-like forecast using moving averages"""
        if len(prices) < 30:
            return {'trend': 0, 'confidence': 0}
        
        # Simple trend detection using EMA crossovers
        ema_short = prices.ewm(span=8).mean()
        ema_long = prices.ewm(span=21).mean()
        
        current_short = ema_short.iloc[-1]
        current_long = ema_long.iloc[-1]
        prev_short = ema_short.iloc[-2] if len(ema_short) > 1 else current_short
        prev_long = ema_long.iloc[-2] if len(ema_long) > 1 else current_long
        
        # Trend direction
        if current_short > current_long and prev_short <= prev_long:
            trend = 0.5  # Bullish crossover
        elif current_short < current_long and prev_short >= prev_long:
            trend = -0.5  # Bearish crossover
        else:
            # Calculate momentum
            momentum = (current_short - current_long) / current_long
            trend = np.clip(momentum * 10, -1.0, 1.0)
        
        confidence = min(1.0, abs(trend))
        
        return {
            'trend': trend,
            'confidence': confidence,
            'ema_short': current_short,
            'ema_long': current_long
        }
    
    def mean_reversion_analysis(self, prices: pd.Series, lookback: int = 20) -> Dict:
        """Mean reversion analysis using Z-score"""
        if len(prices) < lookback:
            return {'signal': 0, 'zscore': 0}
        
        recent_prices = prices.tail(lookback)
        mean_price = recent_prices.mean()
        std_price = recent_prices.std()
        
        if std_price == 0:
            return {'signal': 0, 'zscore': 0}
        
        current_price = prices.iloc[-1]
        zscore = (current_price - mean_price) / std_price
        
        # Mean reversion signal: negative zscore = oversold (bullish), positive = overbought (bearish)
        signal = -zscore * 0.5  # Invert for mean reversion
        signal = np.clip(signal, -1.0, 1.0)
        
        return {
            'signal': signal,
            'zscore': zscore,
            'mean': mean_price,
            'std': std_price
        }
    
    def fibonacci_levels(self, prices: pd.Series, lookback: int = 50) -> Dict:
        """Calculate Fibonacci retracement levels"""
        if len(prices) < lookback:
            return {'signal': 0, 'at_level': False}
        
        recent = prices.tail(lookback)
        high = recent.max()
        low = recent.min()
        current = prices.iloc[-1]
        
        if high == low:
            return {'signal': 0, 'at_level': False}
        
        range_size = high - low
        fib_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
        
        # Check if price is near a Fibonacci level
        distances = []
        for fib in fib_levels:
            level = low + (range_size * fib)
            distance = abs(current - level) / current
            distances.append((fib, level, distance))
        
        # Find closest level
        closest = min(distances, key=lambda x: x[2])
        fib_level, level_price, distance = closest
        
        # Signal: near support (lower fibs) = bullish, near resistance (higher fibs) = bearish
        if distance < 0.01:  # Within 1%
            if fib_level < 0.5:
                signal = 0.3  # Near support, bullish
            else:
                signal = -0.2  # Near resistance, bearish
        else:
            signal = 0
        
        return {
            'signal': signal,
            'at_level': distance < 0.01,
            'closest_level': fib_level,
            'level_price': level_price
        }
    
    def volatility_regime(self, prices: pd.Series, lookback: int = 20) -> str:
        """Detect volatility regime"""
        if len(prices) < lookback:
            return 'NORMAL'
        
        returns = prices.pct_change().dropna().tail(lookback)
        current_vol = returns.std()
        
        # Compare to longer-term volatility
        if len(prices) >= lookback * 2:
            long_vol = prices.pct_change().dropna().tail(lookback * 2).std()
            vol_ratio = current_vol / long_vol if long_vol > 0 else 1.0
            
            if vol_ratio > 1.5:
                return 'HIGH'
            elif vol_ratio < 0.7:
                return 'LOW'
        
        return 'NORMAL'


def format_analysis_report(data: Dict) -> str:
    """Format quant analysis report for display"""
    if not data:
        return "No quantitative analysis available"
    
    lines = ["📊 Quantitative Analysis Report"]
    lines.append("=" * 40)
    
    # Monte Carlo
    mc = data.get('monte_carlo', {})
    if mc:
        bias = mc.get('bias', 0)
        lines.append(f"Monte Carlo: {bias:+.2f}% bias")
    
    # ARIMA
    arima = data.get('arima', {})
    if arima:
        trend = arima.get('trend', 0)
        trend_str = "BULLISH" if trend > 0 else "BEARISH" if trend < 0 else "NEUTRAL"
        lines.append(f"ARIMA Forecast: {trend_str} ({trend:+.2f})")
    
    # Mean Reversion
    mr = data.get('mean_reversion', {})
    if mr:
        signal = mr.get('signal', 0)
        zscore = mr.get('zscore', 0)
        lines.append(f"Mean Reversion: Z-score {zscore:.2f} ({'OVERSOLD' if signal > 0 else 'OVERBOUGHT' if signal < 0 else 'NEUTRAL'})")
    
    # Fibonacci
    fib = data.get('fibonacci', {})
    if fib and fib.get('at_level'):
        lines.append(f"Fibonacci: At {fib.get('closest_level', 0)*100:.1f}% level")
    
    # Volatility
    vol_regime = data.get('volatility_regime', 'NORMAL')
    lines.append(f"Volatility: {vol_regime}")
    
    return "\n".join(lines)
