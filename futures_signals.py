import os
import asyncio
import ccxt
import pandas as pd
import numpy as np
import json
import urllib.request
from datetime import datetime, timezone, timedelta
import ta
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from telegram import Bot
from flask import Flask, jsonify, render_template_string, render_template, send_from_directory, Response, request
import threading
import qrcode
import io

from quant_analytics import QuantAnalytics, format_analysis_report
from ml_signals import ml_predictor
from sentiment_analyzer import sentiment_analyzer
from onchain_analytics import onchain_analytics
from trade_exit_engine import ExitContext, ExitLevels, calculate_exit_levels
from signal_density_engine import DensityContext, DensityResult, evaluate_signal_density
from async_safety_engine import AsyncResult, safe_call, guard_boolean, guard_numeric, safe_len
from market_regime_engine import RegimeContext, RegimeResult, detect_market_regime as detect_regime_v2
from net_profit_engine import NetProfitDecision, net_profit_engine
from entry_optimizer_5m import optimize_entry_5m, EntryOptimizationResult
from pro_strategies import (
    pro_analyzer, ProSignal, CandleReversal, BoxStrategy, BreakerBlock,
    ElliottWavePhase, FibonacciSweetSpot, ExhaustionGap, 
    CandleStrengthAnalyzer, AmateurHourFilter, CloseBasedSR,
    MoneyFlowIndex, ChaikinMoneyFlow, MoneyFlowDivergence, WaveScore, StopHuntDetector,
    MarketStructure, OrderBlocks, FairValueGap
)

# ================================
# CONFIG
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Futures contracts (Perpetual) - 7 assets (BNB removed - no Kraken Perp)
FUTURES_ASSETS = [
    "PF_XBTUSD",   # BTC Perpetual
    "PF_ETHUSD",   # ETH Perpetual
    "PF_SOLUSD",   # SOL Perpetual
    "PF_XRPUSD",   # XRP Perpetual
    "PF_LTCUSD",   # LTC Perpetual
    "PF_ADAUSD",   # ADA Perpetual
    "PF_DOTUSD",   # DOT Perpetual
]

ASSET_NAMES = {
    "PF_XBTUSD": "BTC",
    "PF_ETHUSD": "ETH",
    "PF_SOLUSD": "SOL",
    "PF_XRPUSD": "XRP",
    "PF_LTCUSD": "LTC",
    "PF_ADAUSD": "ADA",
    "PF_DOTUSD": "DOT",
}

# Timeframes
TIMEFRAME_MACRO = "4h"    # Macro analysis (big picture)
TIMEFRAME_TREND = "1h"    # Trend analysis
TIMEFRAME_ENTRY = "15m"   # Entry signals
TIMEFRAME_5M_OPTIMIZE = "5m"   # v8.9.24: Entry optimization only (no signal generation)
TIMEFRAME_DAILY = "1d"    # Daily liquidity zones (v8.5)

# Signal settings
CHECK_INTERVAL = 60       # Check every 60 seconds
MIN_SCORE = 60            # v8.9.20: Balanced threshold for quality signals
LEVERAGE = 10             # Kraken Futures fixed leverage

# Indicator settings
RSI_OVERSOLD = 27.0       # v8.9.22: Profesionalus lygis - TIK ekstremali oversold
RSI_OVERBOUGHT = 69.50    # Overbought level (slightly below 70 for earlier entry)
ADX_MIN = 20
ADX_STRONG = 30

# Support/Resistance Breakout
BREAKOUT_LOOKBACK = 20    # Periods to find support/resistance
BREAKOUT_THRESHOLD = 0.002  # 0.2% breakout confirmation

# Trailing Stop Settings
TRAILING_ENABLED = True
TRAILING_DISTANCE_PCT = 1.5   # Trailing stop distance from current price (%)
BREAKEVEN_AT_TP1 = True       # Move SL to entry when TP1 is hit
TRAILING_ACTIVATION_PCT = 1.0 # Activate trailing after 1% profit

# ================================
# PARTIAL TAKE PROFIT SETTINGS (v8.4)
# ================================
PARTIAL_TP_ENABLED = True     # Auto-close partial positions at TP1/TP2
PARTIAL_TP1_PCT = 0.33        # Close 33% at TP1
PARTIAL_TP2_PCT = 0.33        # Close 33% at TP2 (remaining 34% runs to TP3/SL)
PARTIAL_MIN_SIZE_USD = 5.0    # Minimum position size to close (Kraken minimum)

# ================================
# AUTO-TRADING SETTINGS (v8.6)
# ================================
AUTO_TRADING_ENABLED = True       # Enable automatic position opening/closing
AUTO_TRADE_MARGIN_USD = 25         # Initial margin in USD per trade (position = margin × leverage)
AUTO_TRADE_MAX_POSITIONS = 3       # Maximum concurrent open positions
AUTO_TRADE_MIN_SCORE = 60          # Minimum score to auto-execute trade
AUTO_CLOSE_ON_SL = True            # Automatically close position when SL is hit
AUTO_CLOSE_ON_TP3 = True           # Automatically close remaining position at TP3

# ================================
# ACCOUNT & RISK LIMITS (v8.9.18)
# ================================
DAILY_LOSS_LIMIT_PCT = 2.0         # Max daily loss as % of capital (-2%)
WEEKLY_LOSS_LIMIT_PCT = 5.0        # Max weekly loss as % of capital (-5%)
DAILY_LOSS_LIMIT_USD = 20          # Fallback: absolute $ limit if balance unavailable

# ================================
# DYNAMIC LEVERAGE SETTINGS (v8.9)
# ================================
DYNAMIC_LEVERAGE_ENABLED = True   # Enable automatic leverage selection based on signal strength
MAX_RISK_PER_TRADE_USD = 4.0      # Maximum risk per trade in USD (fits 5 trades in $20 daily limit)

# Leverage tiers based on signal confidence
LEVERAGE_TIERS = {
    "STRONG": {
        "leverage": 10,
        "min_score": 85,
        "min_ml_confidence": 0.68,
        "min_confirmations": 5,
    },
    "MEDIUM": {
        "leverage": 5,
        "min_score": 80,
        "min_ml_confidence": 0.60,
        "min_confirmations": 4,
    },
    "WEAK": {
        "leverage": 3,
        "min_score": 70,
        "min_ml_confidence": 0.55,
        "min_confirmations": 3,
    },
    "MINIMAL": {
        "leverage": 1,
        "min_score": 60,
        "min_ml_confidence": 0.0,
        "min_confirmations": 0,
    },
}

# ================================
# MARKET REGIME DETECTION (from v7.4)
# ================================
MARKET_REGIME_ENABLED = True
REGIME_CHECK_INTERVAL_HOURS = 1
BTC_BEAR_THRESHOLD = -0.05    # BTC below 200 EMA by 5% = BEAR
BTC_BULL_THRESHOLD = 0.02     # BTC above 200 EMA by 2% = BULL
DEFENSIVE_MODE_ENABLED = True # Block new LONGs in BEAR market

# ================================
# SMART COUNTER-TREND (v8.9.2)
# ================================
QUANT_COUNTER_TREND_ENABLED = True   # Allow LONGs against trend with strong quant confirmation
QUANT_COUNTER_TREND_MIN_BIAS = 15    # Minimum quant score to allow counter-trend LONG (+15 or higher)

# ================================
# S&P 500 + MACRO INDICATORS (from v7.5/v7.6)
# ================================
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# S&P 500 Correlation
SPY_ENABLED = True
SPY_DROP_THRESHOLD = -0.01    # SPY drop > 1% = risk-off
SPY_RALLY_THRESHOLD = 0.01
SPY_BLOCK_LONGS_ON_DROP = True

# VIX (Fear Index)
VIX_ENABLED = True
VIX_HIGH_THRESHOLD = 30       # VIX > 30 = extreme fear, block LONGs
VIX_LOW_THRESHOLD = 20
VIX_BLOCK_LONGS_ON_HIGH = True

# DXY (Dollar Strength)
DXY_ENABLED = True
DXY_STRENGTH_THRESHOLD = 0.005  # DXY up > 0.5% = bearish crypto
DXY_WEAKNESS_THRESHOLD = -0.005

# ================================
# KRAKEN FEES FILTER (v8.3)
# ================================
FEE_FILTER_ENABLED = True
KRAKEN_MAKER_FEE = 0.0002     # 0.02% maker fee
KRAKEN_TAKER_FEE = 0.0005     # 0.05% taker fee
# Round-trip fees (open + close)
MIN_PROFIT_PCT = 0.15         # TP1 must be at least 0.15% to cover fees + small profit
# With 10x leverage: 0.15% price move = 1.5% account profit

# ================================
# RR ENGINE v2.0 (v8.9.24 - HYBRID FUND MODE)
# ================================
# Combines: Dynamic min R:R calculation + Soft penalty system
# Factors: Market regime, trend strength, HTF bias, setup type, volatility
from dataclasses import dataclass

@dataclass
class RRContext:
    rr: float                    # Calculated Risk:Reward
    score: float                 # Current signal score
    trend_strength: str          # "STRONG", "NORMAL", "WEAK"
    atr_ratio: float             # Current ATR / ATR_AVG
    is_countertrend: bool = False
    market_regime: str = "BULL"  # v8.9.24: BULL / BEAR / RANGE
    higher_tf_bias: str = "NEUTRAL"  # v8.9.24: BULL / BEAR / NEUTRAL
    setup_type: str = "CONTINUATION"  # v8.9.24: CONTINUATION / PULLBACK / REVERSAL
    volatility_level: str = "NORMAL"  # v8.9.24: LOW / NORMAL / HIGH

@dataclass
class RRResult:
    allowed: bool
    final_score: float
    min_rr: float
    base_rr: float           # v8.9.24: Base RR before adjustments
    penalty: float
    scalp_mode: bool
    reason: str
    rr_adjustments: str = ""  # v8.9.24: Explanation of adjustments

def get_min_rr(context: RRContext) -> tuple:
    """
    v8.9.24 HYBRID: Dynamic min R:R calculation
    Returns (min_rr, adjustments_explanation)
    """
    base_rr = 1.2
    rr = base_rr
    reasons = []
    
    # Counter-trend override (highest priority)
    if context.is_countertrend:
        return (1.8, "Counter-trend → 1.8 min")
    
    # 1. Market Regime
    if context.market_regime == "RANGE":
        rr += 0.5
        reasons.append("RANGE +0.5")
    elif context.market_regime in ("BULL", "BEAR"):
        rr += 0.2
        reasons.append(f"{context.market_regime} +0.2")
    
    # 2. Trend Strength
    if "STRONG" in context.trend_strength:
        rr -= 0.2
        reasons.append("STRONG -0.2")
    elif context.trend_strength == "WEAK":
        rr += 0.4
        reasons.append("WEAK +0.4")
    
    # 3. Higher Timeframe Bias
    if context.higher_tf_bias == "NEUTRAL":
        rr += 0.3
        reasons.append("HTF_NEUTRAL +0.3")
    
    # 4. Setup Type
    if context.setup_type == "CONTINUATION":
        rr -= 0.2
        reasons.append("CONT -0.2")
    elif context.setup_type == "PULLBACK":
        rr += 0.1
        reasons.append("PULLBACK +0.1")
    elif context.setup_type == "REVERSAL":
        rr += 0.6
        reasons.append("REVERSAL +0.6")
    
    # 5. Volatility (from atr_ratio)
    if context.volatility_level == "HIGH" or context.atr_ratio >= 1.4:
        rr += 0.3
        reasons.append("HIGH_VOL +0.3")
    elif context.volatility_level == "LOW" or context.atr_ratio < 0.7:
        rr -= 0.1
        reasons.append("LOW_VOL -0.1")
    
    # 6. Scalp mode override (aggressive continuation)
    if context.trend_strength == "STRONG" and context.atr_ratio >= 1.4 and context.setup_type == "CONTINUATION":
        rr = max(0.6, rr - 0.4)  # Allow aggressive scalps
        reasons.append("SCALP_MODE")
    
    # 7. Safety Clamp
    rr = round(max(1.0, min(rr, 3.5)), 2)
    
    return (rr, " | ".join(reasons) if reasons else "base")

def rr_penalty(rr: float, min_rr: float) -> float:
    """Soft penalty system instead of hard blocking"""
    if rr >= min_rr:
        return 0.0
    diff = min_rr - rr
    if diff <= 0.15:
        return -5.0
    elif diff <= 0.35:
        return -10.0
    elif diff <= 0.6:
        return -18.0
    else:
        return -999.0  # Only extreme cases blocked

def is_scalp_mode(context: RRContext) -> bool:
    """Detects high-probability scalp environment"""
    return (
        context.trend_strength == "STRONG"
        and context.atr_ratio >= 1.4
        and context.score >= 65
        and not context.is_countertrend
    )

def evaluate_rr(context: RRContext) -> RRResult:
    """Main RR evaluation entry point - v8.9.24 HYBRID"""
    min_rr, rr_adjustments = get_min_rr(context)
    penalty = rr_penalty(context.rr, min_rr)
    scalp = is_scalp_mode(context)
    base_rr = 1.2
    
    if penalty <= -999:
        return RRResult(
            allowed=False,
            final_score=context.score,
            min_rr=min_rr,
            base_rr=base_rr,
            penalty=penalty,
            scalp_mode=scalp,
            reason=f"RR_TOO_BAD ({context.rr:.2f} < {min_rr:.2f})",
            rr_adjustments=rr_adjustments
        )
    
    final_score = context.score + penalty
    if final_score < 0:
        return RRResult(
            allowed=False,
            final_score=final_score,
            min_rr=min_rr,
            base_rr=base_rr,
            penalty=penalty,
            scalp_mode=scalp,
            reason="SCORE_KILLED_BY_RR",
            rr_adjustments=rr_adjustments
        )
    
    return RRResult(
        allowed=True,
        final_score=final_score,
        min_rr=min_rr,
        base_rr=base_rr,
        penalty=penalty,
        scalp_mode=scalp,
        reason="RR_OK",
        rr_adjustments=rr_adjustments
    )

RR_FILTER_ENABLED = True
# Legacy constants (kept for backwards compatibility)
GOOD_RR_RATIO = 2.0
EXCELLENT_RR_RATIO = 2.5

# ================================
# REBOUND ENTRY REFINER v2.0 (v8.9.24)
# ================================
# Entry refiner that improves score/RR when extreme conditions detected
# NO LONGER a separate trade mode - just boosts existing signals

@dataclass
class ReboundRefinerContext:
    market_regime: str           # "BULL" / "BEAR"
    rsi: float                   # RSI value
    atr_ratio: float             # ATR / ATR_AVG
    has_bullish_divergence: bool
    candle_reversal: bool        # True if candle reversal detected
    bullish_candle_close: bool   # 5m bullish close

@dataclass
class ReboundRefinerResult:
    active: bool
    score_boost: int            # +5 to +15 pts
    rr_improvement: float       # R:R multiplier boost (e.g., 1.1 = +10%)
    reason: str

def evaluate_rebound_refiner(context: ReboundRefinerContext) -> ReboundRefinerResult:
    """
    Rebound Entry Refiner v2.0 (v8.9.24)
    
    Evaluates extreme conditions and provides score/RR boost instead of 
    creating separate trade mode. Works as ENTRY_REFINER.
    """
    score_boost = 0
    rr_improvement = 1.0
    reasons = []
    
    # Tier 1: Extreme oversold + reversal pattern (+15 pts, +15% R:R)
    if (context.market_regime == "BEAR" 
        and context.rsi <= 25 
        and context.atr_ratio >= 1.5
        and context.candle_reversal):
        score_boost += 15
        rr_improvement = 1.15
        reasons.append("EXTREME_REVERSAL")
    
    # Tier 2: Strong oversold + divergence (+10 pts, +10% R:R)
    elif (context.market_regime == "BEAR"
          and context.rsi <= 28
          and context.has_bullish_divergence):
        score_boost += 10
        rr_improvement = 1.10
        reasons.append("DIVERGENCE_SETUP")
    
    # Tier 3: Oversold + bullish candle close (+5 pts, +5% R:R)
    elif (context.market_regime == "BEAR"
          and context.rsi <= 30
          and context.bullish_candle_close
          and context.atr_ratio >= 1.2):
        score_boost += 5
        rr_improvement = 1.05
        reasons.append("BULLISH_CLOSE")
    
    # Tier 4: Good pullback entry in BEAR (+3 pts)
    elif (context.market_regime == "BEAR"
          and context.rsi <= 35
          and context.candle_reversal):
        score_boost += 3
        rr_improvement = 1.0
        reasons.append("PULLBACK_ENTRY")
    
    if score_boost > 0:
        return ReboundRefinerResult(
            active=True,
            score_boost=score_boost,
            rr_improvement=rr_improvement,
            reason="+".join(reasons)
        )
    
    return ReboundRefinerResult(
        active=False,
        score_boost=0,
        rr_improvement=1.0,
        reason="NO_BOOST"
    )

# Legacy compatibility wrapper
@dataclass
class ScalpReboundContext:
    market_regime: str
    rsi: float
    atr_ratio: float
    has_bullish_divergence: bool
    candle_reversal: bool

@dataclass
class ScalpReboundResult:
    enabled: bool
    trade_mode: str
    size_multiplier: float
    min_rr_override: float
    reason: str

def evaluate_scalp_rebound(context: ScalpReboundContext) -> ScalpReboundResult:
    """Legacy wrapper - now returns NORMAL trade mode, uses refiner instead"""
    return ScalpReboundResult(
        enabled=False,
        trade_mode="NORMAL",
        size_multiplier=1.0,
        min_rr_override=None,
        reason="REFINER_MODE"
    )

# ================================
# VWAP FILTER (v8.9.9)
# ================================
# VWAP (Volume Weighted Average Price) - institucinių traderių standartas
# Kaina virš VWAP = pirkėjų kontrolė, žemiau = pardavėjų kontrolė
VWAP_FILTER_ENABLED = True    # Enable VWAP-based signal filtering
VWAP_PERIOD = 50              # Rolling VWAP period (bars)
VWAP_STRICT_MODE = False      # If True, block signals against VWAP bias completely

# ================================
# CIRCUIT BREAKERS (from v6.0)
# ================================
CIRCUIT_BREAKER_ENABLED = True
MAX_CONSECUTIVE_LOSSES = 3    # Pause after 3 losses in a row
VOLATILITY_CIRCUIT_THRESHOLD = 0.08  # 8% daily volatility = pause

# ================================
# FOMC BLACKOUT FILTER
# ================================
FOMC_BLACKOUT_ENABLED = True
FOMC_BLACKOUT_HOURS_BEFORE = 2
FOMC_BLACKOUT_HOURS_AFTER = 2

FOMC_DATES = [
    # 2025
    datetime(2025, 1, 29, 19, 0, tzinfo=timezone.utc),
    datetime(2025, 3, 19, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 5, 7, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 6, 18, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 7, 30, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 9, 17, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 10, 29, 18, 0, tzinfo=timezone.utc),
    datetime(2025, 12, 10, 19, 0, tzinfo=timezone.utc),
    # 2026
    datetime(2026, 1, 28, 19, 0, tzinfo=timezone.utc),
    datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc),
]

# ================================
# EXCHANGE SETUP
# ================================
# Kraken Futures API credentials (for position tracking)
KRAKEN_FUTURES_API_KEY = os.getenv("KRAKEN_FUTURES_API_KEY")
KRAKEN_FUTURES_SECRET = os.getenv("KRAKEN_FUTURES_SECRET")

# Create authenticated exchange if credentials available
if KRAKEN_FUTURES_API_KEY and KRAKEN_FUTURES_SECRET:
    exchange = ccxt.krakenfutures({
        "apiKey": KRAKEN_FUTURES_API_KEY,
        "secret": KRAKEN_FUTURES_SECRET,
        "enableRateLimit": True,
    })
    POSITION_TRACKING_ENABLED = True
else:
    exchange = ccxt.krakenfutures({
        "enableRateLimit": True,
    })
    POSITION_TRACKING_ENABLED = False

# ================================
# MULTI-COLLATERAL BALANCE SYSTEM
# ================================
account_balance_cache = {
    "total_usd": 0.0,
    "cash_usd": 0.0,
    "flex_usd": 0.0,
    "collaterals": {},
    "last_update": None,
    "fetch_failed": False,
    "consecutive_failures": 0,
}

def fetch_multi_collateral_balance():
    """
    Gauti balansą iš visų Kraken Futures collateral account tipų:
    - Cash (Single-Collateral): USD only
    - Flex (Multi-Collateral): BTC, ETH, USDT, kt.
    Grąžina bendrą USD vertę.
    """
    global account_balance_cache
    
    if not POSITION_TRACKING_ENABLED:
        return account_balance_cache
    
    try:
        balance = exchange.fetch_balance()
        
        cash_usd = 0.0
        flex_usd = 0.0
        collaterals = {}
        
        for currency, data in balance.items():
            if isinstance(data, dict):
                total = data.get('total', 0) or 0
                if total > 0:
                    collaterals[currency] = {
                        "total": total,
                        "free": data.get('free', 0) or 0,
                        "used": data.get('used', 0) or 0,
                    }
                    if currency == 'USD':
                        cash_usd = total
                    elif currency == 'USDC':
                        flex_usd += total
                    elif currency == 'USDT':
                        flex_usd += total
                    elif currency in ['BTC', 'XBT']:
                        try:
                            ticker = exchange.fetch_ticker('PF_XBTUSD')
                            btc_usd = total * ticker['last']
                            flex_usd += btc_usd
                            collaterals[currency]['usd_value'] = btc_usd
                        except:
                            pass
                    elif currency == 'ETH':
                        try:
                            ticker = exchange.fetch_ticker('PF_ETHUSD')
                            eth_usd = total * ticker['last']
                            flex_usd += eth_usd
                            collaterals[currency]['usd_value'] = eth_usd
                        except:
                            pass
                    elif currency == 'EUR':
                        eur_usd_rate = 1.04
                        eur_usd = total * eur_usd_rate
                        flex_usd += eur_usd
                        collaterals[currency]['usd_value'] = eur_usd
        
        total_usd = cash_usd + flex_usd
        
        account_balance_cache = {
            "total_usd": round(total_usd, 2),
            "cash_usd": round(cash_usd, 2),
            "flex_usd": round(flex_usd, 2),
            "collaterals": collaterals,
            "last_update": datetime.now(timezone.utc),
            "fetch_failed": False,
            "consecutive_failures": 0,
        }
        
        return account_balance_cache
        
    except Exception as e:
        print(f"⚠️ Error fetching balance: {e}")
        account_balance_cache["fetch_failed"] = True
        account_balance_cache["consecutive_failures"] = account_balance_cache.get("consecutive_failures", 0) + 1
        return account_balance_cache

def get_available_balance():
    """Gauti prieinamą balansą (cache 60s)"""
    global account_balance_cache
    
    if account_balance_cache["last_update"] is None:
        return fetch_multi_collateral_balance()
    
    age = (datetime.now(timezone.utc) - account_balance_cache["last_update"]).seconds
    if age > 60:
        return fetch_multi_collateral_balance()
    
    return account_balance_cache

# ================================
# STATE
# ================================
signals_history = []
last_signals = {}
bot_stats = {
    "total_signals": 0,
    "long_signals": 0,
    "short_signals": 0,
    "wins": 0,
    "losses": 0,
    "total_profit_pct": 0.0,
    "start_time": datetime.now(timezone.utc),
    "last_check": None,
    "cycles_completed": 0,
    "cycles_success": 0,
    "consecutive_errors": 0,
    "last_heartbeat": None,
}

HEARTBEAT_INTERVAL = 3600  # 1 hour in seconds

async def send_heartbeat():
    """Siųsti heartbeat žinutę į Telegram kas 1 val"""
    now = datetime.now(timezone.utc)
    uptime = now - bot_stats["start_time"]
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    
    cycles = bot_stats["cycles_completed"]
    success = bot_stats["cycles_success"]
    success_rate = (success / cycles * 100) if cycles > 0 else 100.0
    
    # Get positions count
    positions_count = len(open_positions)
    kraken_count = len(kraken_positions.get('positions', []))
    
    # Get balance
    balance_data = get_available_balance()
    total_balance = balance_data.get("total_usd", 0)
    
    # Daily P&L
    daily_pnl = auto_trading_state.get('daily_pnl', 0)
    daily_pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
    
    # Status emoji
    if auto_trading_state.get('is_paused'):
        status_emoji = "⏸️"
        status_text = f"PAUSED: {auto_trading_state.get('pause_reason', 'Unknown')}"
    else:
        status_emoji = "💚"
        status_text = "Bot veikia normaliai"
    
    message = f"""{status_emoji} BOT HEARTBEAT

{"✅" if status_emoji == "💚" else "⚠️"} {status_text}
⏱️ Uptime: {hours}h {minutes}m
📊 Ciklų atlikta: {cycles}
✨ Sėkmės rodiklis: {success_rate:.1f}%

💰 Balansas: ${total_balance:.2f}
📈 Dienos P&L: {daily_pnl_str}
📍 Atviros pozicijos: {kraken_count}

🕐 Kitas tikrinimas: ~{CHECK_INTERVAL // 60} min

📅 {now.strftime('%Y-%m-%d %H:%M')} UTC"""

    try:
        from telegram import Bot
        tg_bot = Bot(token=TELEGRAM_TOKEN)
        await tg_bot.send_message(chat_id=CHAT_ID, text=message)
        bot_stats["last_heartbeat"] = now
        print(f"💚 Heartbeat sent at {now.strftime('%H:%M:%S')} UTC")
    except Exception as e:
        print(f"⚠️ Heartbeat send error: {e}")
        bot_stats["last_heartbeat"] = now  # Still update to prevent spam

# ================================
# WIN/LOSS TRACKING SYSTEM
# ================================
SIGNALS_FILE = "signal_results.json"

def load_signal_results():
    """Įkelti signalų rezultatus iš failo"""
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading signal results: {e}")
    return {"signals": [], "stats": {"wins": 0, "losses": 0, "total_profit_pct": 0.0, "ct_wins": 0, "ct_losses": 0}}

def save_signal_results(data):
    """Išsaugoti signalų rezultatus į failą"""
    try:
        with open(SIGNALS_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"Error saving signal results: {e}")

def mark_signal_result(signal_id: str, result: str, profit_pct: float = 0.0):
    """
    Pažymėti signalo rezultatą
    result: 'WIN' arba 'LOSS'
    profit_pct: pelno/nuostolio procentas (pvz. 2.5 arba -1.2)
    """
    data = load_signal_results()
    
    # Ensure ct_wins/ct_losses exist in stats
    if "ct_wins" not in data["stats"]:
        data["stats"]["ct_wins"] = 0
    if "ct_losses" not in data["stats"]:
        data["stats"]["ct_losses"] = 0
    
    # Ieškoti signalo pagal ID
    for sig in data["signals"]:
        if sig.get("id") == signal_id:
            sig["result"] = result
            sig["profit_pct"] = profit_pct
            sig["marked_at"] = datetime.now(timezone.utc).isoformat()
            
            # Atnaujinti statistiką
            if result == "WIN":
                data["stats"]["wins"] += 1
                bot_stats["wins"] += 1
                # v8.9.4: Track counter-trend separately
                if sig.get("is_counter_trend", False):
                    data["stats"]["ct_wins"] += 1
            else:
                data["stats"]["losses"] += 1
                bot_stats["losses"] += 1
                # v8.9.4: Track counter-trend separately
                if sig.get("is_counter_trend", False):
                    data["stats"]["ct_losses"] += 1
            
            data["stats"]["total_profit_pct"] += profit_pct
            bot_stats["total_profit_pct"] += profit_pct
            
            save_signal_results(data)
            return True, sig
    
    return False, None

def get_counter_trend_stats():
    """v8.9.4: Get counter-trend signal statistics"""
    data = load_signal_results()
    stats = data.get("stats", {})
    ct_wins = stats.get("ct_wins", 0)
    ct_losses = stats.get("ct_losses", 0)
    ct_total = ct_wins + ct_losses
    
    if ct_total == 0:
        return {"ct_win_rate": 0, "ct_wins": 0, "ct_losses": 0, "ct_total": 0, "ct_disabled": False}
    
    ct_win_rate = round(ct_wins / ct_total * 100, 1)
    ct_loss_rate = round(ct_losses / ct_total * 100, 1)
    
    # Auto-disable if loss rate > 60% AND we have at least 5 counter-trend trades
    ct_disabled = ct_loss_rate > 60 and ct_total >= 5
    
    return {
        "ct_win_rate": ct_win_rate,
        "ct_loss_rate": ct_loss_rate,
        "ct_wins": ct_wins,
        "ct_losses": ct_losses,
        "ct_total": ct_total,
        "ct_disabled": ct_disabled
    }

def get_win_rate():
    """Gauti win rate statistiką"""
    data = load_signal_results()
    stats = data["stats"]
    total = stats["wins"] + stats["losses"]
    total_signals = len(data.get("signals", []))
    if total == 0:
        return {"win_rate": 0, "wins": 0, "losses": 0, "total": 0, "profit_pct": 0, "total_signals": total_signals}
    
    return {
        "win_rate": round(stats["wins"] / total * 100, 1),
        "wins": stats["wins"],
        "losses": stats["losses"],
        "total": total,
        "profit_pct": round(stats["total_profit_pct"], 2),
        "total_signals": total_signals
    }

def record_trade_result(symbol: str, direction: str, result: str, profit_pct: float, is_counter_trend: bool = False):
    """
    Record trade result when position is closed (used by sync function)
    """
    data = load_signal_results()
    
    # Ensure ct_wins/ct_losses exist
    if "ct_wins" not in data["stats"]:
        data["stats"]["ct_wins"] = 0
    if "ct_losses" not in data["stats"]:
        data["stats"]["ct_losses"] = 0
    
    # Update stats
    if result == "WIN":
        data["stats"]["wins"] += 1
        bot_stats["wins"] += 1
        if is_counter_trend:
            data["stats"]["ct_wins"] += 1
    else:
        data["stats"]["losses"] += 1
        bot_stats["losses"] += 1
        if is_counter_trend:
            data["stats"]["ct_losses"] += 1
    
    data["stats"]["total_profit_pct"] += profit_pct
    bot_stats["total_profit_pct"] += profit_pct
    
    save_signal_results(data)
    print(f"  📊 Trade result recorded: {symbol} {direction} = {result} ({profit_pct:+.2f}%)")

def add_signal_to_tracking(signal: dict):
    """Pridėti naują signalą į tracking sistemą"""
    data = load_signal_results()
    
    # Sukurti unikalų ID
    signal_id = f"{signal['symbol']}_{signal['time'].strftime('%Y%m%d_%H%M%S')}"
    
    # v8.9.4: Detect if this is a counter-trend signal
    is_counter_trend = False
    signals_list = signal.get("signals", [])
    if any("QUANT_COUNTER_TREND" in s for s in signals_list):
        is_counter_trend = True
    
    tracked_signal = {
        "id": signal_id,
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry": signal.get("price", signal.get("entry", 0)),
        "sl": signal.get("sl", 0),
        "tp1": signal.get("tp1", 0),
        "tp2": signal.get("tp2", 0),
        "tp3": signal.get("tp3", 0),
        "score": signal.get("score", 0),
        "time": signal["time"].isoformat(),
        "result": None,
        "profit_pct": None,
        "marked_at": None,
        "is_counter_trend": is_counter_trend  # v8.9.4: Track CT signals
    }
    
    data["signals"].append(tracked_signal)
    
    # Išlaikyti tik paskutinius 200 signalų
    if len(data["signals"]) > 200:
        data["signals"] = data["signals"][-200:]
    
    save_signal_results(data)
    return signal_id

# Įkelti ankstesnius rezultatus paleidžiant
_saved_results = load_signal_results()
bot_stats["wins"] = _saved_results["stats"].get("wins", 0)
bot_stats["losses"] = _saved_results["stats"].get("losses", 0)
bot_stats["total_profit_pct"] = _saved_results["stats"].get("total_profit_pct", 0.0)

quant_engine = QuantAnalytics()
quant_results = {}
quant_correlation = None
quant_last_update = None

# Position tracking for trailing stop
open_positions = {}  # symbol -> position_data

# Disabled assets (PWA control) - v8.9.18
disabled_assets = set()  # Assets to skip during analysis

# Market Regime State
market_regime_state = {
    "regime": "NEUTRAL",
    "btc_vs_ema200": 0,
    "longs_blocked": False,
    "defensive_mode": False,
    "last_check": None,
    "regime_message": "",
}

# S&P 500 State
spy_state = {
    "current_price": None,
    "daily_change_pct": 0,
    "trend": "NEUTRAL",
    "risk_off": False,
    "longs_blocked_by_spy": False,
    "last_check": None,
}

# Macro State (VIX, DXY)
macro_state = {
    "vix_value": None,
    "vix_level": "NORMAL",
    "vix_longs_blocked": False,
    "dxy_price": None,
    "dxy_trend": "NEUTRAL",
    "last_check": None,
}

# Circuit Breaker State
circuit_state = {
    "consecutive_losses": 0,
    "is_paused": False,
    "pause_reason": None,
    "last_signal_result": None,
}

# Risk Events Log (v8.9.18)
risk_events_log = []  # List of risk events for analytics
MAX_RISK_EVENTS = 100  # Keep last 100 events

def log_risk_event(event_type, details, severity="warning"):
    """
    Log a risk event for analytics.
    event_type: "DAILY_LIMIT", "WEEKLY_LIMIT", "CONSECUTIVE_LOSSES", "VOLATILITY_SPIKE", "EMERGENCY_STOP"
    severity: "info", "warning", "critical"
    """
    global risk_events_log
    event = {
        "type": event_type,
        "details": details,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_at_event": get_available_balance().get("total_usd", 0),
        "daily_pnl": auto_trading_state.get("daily_pnl", 0),
        "weekly_pnl": auto_trading_state.get("weekly_pnl", 0),
        "consecutive_losses": circuit_state.get("consecutive_losses", 0),
    }
    risk_events_log.append(event)
    
    # Trim to max size
    if len(risk_events_log) > MAX_RISK_EVENTS:
        risk_events_log = risk_events_log[-MAX_RISK_EVENTS:]
    
    print(f"⚠️ RISK EVENT: {event_type} - {details} (severity: {severity})")

# FOMC State
fomc_alert_sent = set()

# Kraken Live Positions State
kraken_positions = {
    "positions": {},  # symbol -> {"direction": "LONG"/"SHORT", "size": float}
    "last_fetch": None,
    "fetch_interval": 30,  # seconds between API calls
}

# Auto-Trading State (v8.6 + v8.9.18 weekly tracking)
auto_trading_state = {
    "daily_pnl": 0.0,           # Today's realized P&L
    "daily_trades": 0,          # Trades today
    "daily_wins": 0,
    "daily_losses": 0,
    "weekly_pnl": 0.0,          # This week's realized P&L (v8.9.18)
    "weekly_trades": 0,         # Trades this week
    "weekly_wins": 0,
    "weekly_losses": 0,
    "last_reset": None,         # Last daily reset time
    "last_weekly_reset": None,  # Last weekly reset time (Monday UTC)
    "is_paused": False,         # Paused due to loss limit
    "pause_reason": None,
    "pause_type": None,         # "DAILY" or "WEEKLY"
}

# ================================
# DYNAMIC LEVERAGE & POSITION SIZING (v8.9.21)
# ================================
def determine_leverage(signal_score, ml_confidence=None, confirmation_count=0, stop_loss_pct=0.01):
    """
    v8.9.21: Calculate position size based on RISK BUDGET and SL distance.
    
    Formula: Position Size = Max Risk ($4) / SL Distance (%)
    This ensures risk is ALWAYS capped at $4 regardless of SL width.
    
    Args:
        signal_score: Signal quality score (0-100)
        ml_confidence: ML model win probability (0.0-1.0), None if not available
        confirmation_count: Number of confirming indicators
        stop_loss_pct: Stop loss distance as percentage (e.g., 0.02 = 2%)
    
    Returns:
        dict with leverage, tier, reason, and adjusted position size
    """
    tier_reason = []
    risk_adjusted = False
    
    # Determine tier based on signal quality
    ml_available = ml_confidence is not None
    effective_ml = ml_confidence if ml_available else 1.0
    
    selected_tier = "MINIMAL"
    tier_leverage = 1
    
    if DYNAMIC_LEVERAGE_ENABLED:
        for tier_name in ["STRONG", "MEDIUM", "WEAK", "MINIMAL"]:
            tier = LEVERAGE_TIERS[tier_name]
            
            score_ok = signal_score >= tier["min_score"]
            ml_ok = effective_ml >= tier["min_ml_confidence"]
            conf_ok = confirmation_count >= tier["min_confirmations"]
            
            if score_ok and ml_ok and conf_ok:
                selected_tier = tier_name
                tier_leverage = tier["leverage"]
                tier_reason = [
                    f"Score: {signal_score} >= {tier['min_score']}",
                    f"ML: {ml_confidence:.0%}" if ml_available else "ML: N/A",
                    f"Conf: {confirmation_count}"
                ]
                break
    else:
        tier_leverage = LEVERAGE
        selected_tier = "FIXED"
        tier_reason = ["Dynamic leverage disabled"]
    
    # ========================================
    # v8.9.21: RISK-BASED POSITION SIZING
    # ========================================
    # Core formula: Position Size = Max Risk / SL Distance
    # This ensures max loss = $4 regardless of SL width
    
    # Safety: minimum SL distance 0.5% to prevent huge positions
    min_sl_pct = 0.005  # 0.5%
    effective_sl_pct = max(stop_loss_pct, min_sl_pct)
    
    # Calculate max position size based on risk budget
    # Risk = Position Size × SL%
    # Position Size = Risk / SL%
    max_position_by_risk = MAX_RISK_PER_TRADE_USD / effective_sl_pct
    
    # Calculate position size based on tier leverage and margin
    tier_position_size = AUTO_TRADE_MARGIN_USD * tier_leverage
    
    # Use SMALLER of the two (risk-capped)
    if max_position_by_risk < tier_position_size:
        position_size_usd = max_position_by_risk
        risk_adjusted = True
        # Recalculate effective leverage
        selected_leverage = max(1, min(10, int(position_size_usd / AUTO_TRADE_MARGIN_USD)))
        tier_reason.append(f"🛡️ Risk-sized: ${position_size_usd:.0f} (SL {effective_sl_pct*100:.1f}%)")
    else:
        position_size_usd = tier_position_size
        selected_leverage = tier_leverage
    
    # Calculate actual margin (collateral) needed
    margin_usd = position_size_usd / selected_leverage if selected_leverage > 0 else AUTO_TRADE_MARGIN_USD
    
    # Hard caps for safety
    # Max position: $250 (10x leverage on $25)
    # Min position: $10 (too small = high fee impact)
    MAX_POSITION_USD = 250
    MIN_POSITION_USD = 10
    
    if position_size_usd > MAX_POSITION_USD:
        position_size_usd = MAX_POSITION_USD
        tier_reason.append(f"Capped at ${MAX_POSITION_USD}")
    
    if position_size_usd < MIN_POSITION_USD:
        position_size_usd = MIN_POSITION_USD
        tier_reason.append(f"Min size ${MIN_POSITION_USD}")
    
    # Recalculate leverage based on final position
    selected_leverage = max(1, min(10, int(position_size_usd / AUTO_TRADE_MARGIN_USD)))
    margin_usd = min(AUTO_TRADE_MARGIN_USD, position_size_usd / selected_leverage) if selected_leverage > 0 else AUTO_TRADE_MARGIN_USD
    
    # Calculate actual risk for this trade
    actual_risk = position_size_usd * effective_sl_pct
    
    return {
        "leverage": selected_leverage,
        "tier": selected_tier,
        "reason": " | ".join(tier_reason),
        "margin_usd": margin_usd,
        "position_size_usd": position_size_usd,
        "risk_adjusted": risk_adjusted,
        "ml_confidence": ml_confidence,
        "confirmation_count": confirmation_count,
        "actual_risk_usd": actual_risk,
        "sl_pct": effective_sl_pct
    }

# ================================
# AUTO-TRADING FUNCTIONS (v8.6)
# ================================
async def open_position(symbol, direction, price, sl, tp1, tp2, tp3, signal_score, ml_confidence=None, confirmation_count=0, entry_type="MARKET", **kwargs):
    """
    Open a new position on Kraken Futures.
    
    Args:
        symbol: Futures symbol (e.g. "PF_XBTUSD")
        direction: "LONG" or "SHORT"
        price: Entry price
        sl: Stop loss price
        tp1, tp2, tp3: Take profit levels
        signal_score: Signal quality score
        entry_type: "MARKET" or "LIMIT" (v8.9.24)
    
    Returns:
        dict with success status and order details
    """
    global auto_trading_state
    
    if not AUTO_TRADING_ENABLED:
        return {"success": False, "reason": "AUTO_TRADING_DISABLED"}
    
    if not POSITION_TRACKING_ENABLED:
        return {"success": False, "reason": "NO_API_KEYS"}
    
    # Check risk limits (v8.9.18 - percentage based)
    if auto_trading_state['is_paused']:
        return {"success": False, "reason": f"RISK_LIMIT_PAUSED: {auto_trading_state['pause_reason']}"}
    
    # v8.9.21 #4: FAIL-CLOSED - Halt trading if balance unknown
    balance = get_available_balance()
    if balance.get("fetch_failed") and balance.get("consecutive_failures", 0) >= 3:
        print(f"  🚫 FAIL-CLOSED: Balance fetch failed {balance['consecutive_failures']}x - trading halted")
        log_risk_event("BALANCE_FETCH_FAILED", f"Balance unknown after {balance['consecutive_failures']} failures - trading halted", "critical")
        return {"success": False, "reason": "BALANCE_UNKNOWN_FAIL_CLOSED"}
    
    capital = balance.get("total_usd", 0)
    
    if capital > 0:
        # Percentage-based limits
        daily_limit = capital * (DAILY_LOSS_LIMIT_PCT / 100)
        weekly_limit = capital * (WEEKLY_LOSS_LIMIT_PCT / 100)
        
        # Check weekly limit first (more restrictive)
        if auto_trading_state['weekly_pnl'] <= -weekly_limit:
            auto_trading_state['is_paused'] = True
            auto_trading_state['pause_type'] = "WEEKLY"
            auto_trading_state['pause_reason'] = f"Weekly -5% limit reached (${weekly_limit:.2f})"
            log_risk_event("WEEKLY_LIMIT", f"Weekly loss -${abs(auto_trading_state['weekly_pnl']):.2f} hit limit -${weekly_limit:.2f}", "critical")
            return {"success": False, "reason": "WEEKLY_LOSS_LIMIT"}
        
        # Check daily limit
        if auto_trading_state['daily_pnl'] <= -daily_limit:
            auto_trading_state['is_paused'] = True
            auto_trading_state['pause_type'] = "DAILY"
            auto_trading_state['pause_reason'] = f"Daily -2% limit reached (${daily_limit:.2f})"
            log_risk_event("DAILY_LIMIT", f"Daily loss -${abs(auto_trading_state['daily_pnl']):.2f} hit limit -${daily_limit:.2f}", "critical")
            return {"success": False, "reason": "DAILY_LOSS_LIMIT"}
    else:
        # Fallback to absolute USD limit if balance unavailable
        if auto_trading_state['daily_pnl'] <= -DAILY_LOSS_LIMIT_USD:
            auto_trading_state['is_paused'] = True
            auto_trading_state['pause_type'] = "DAILY"
            auto_trading_state['pause_reason'] = f"Daily loss limit ${DAILY_LOSS_LIMIT_USD} reached"
            log_risk_event("DAILY_LIMIT", f"Daily loss hit ${DAILY_LOSS_LIMIT_USD} fallback limit", "critical")
            return {"success": False, "reason": "DAILY_LOSS_LIMIT"}
    
    # Check max positions - FORCE fresh fetch (no cache) to prevent duplicates
    kraken_positions['last_fetch'] = None  # Clear cache
    await fetch_kraken_positions()
    current_positions = len(kraken_positions['positions'])
    if current_positions >= AUTO_TRADE_MAX_POSITIONS:
        return {"success": False, "reason": f"MAX_POSITIONS ({AUTO_TRADE_MAX_POSITIONS})"}
    
    # Check if already have position in this asset
    if has_open_position(symbol):
        existing_pos = kraken_positions['positions'].get(symbol, {})
        existing_dir = existing_pos.get('direction', '')
        if existing_dir and existing_dir != direction:
            print(f"  ⚠️ CONFLICT: Tried {direction} but {existing_dir} position exists!")
        return {"success": False, "reason": "POSITION_EXISTS"}
    
    # v8.9.21: Also check open_positions for local tracking
    if symbol in open_positions:
        local_dir = open_positions[symbol].get('direction', '')
        if local_dir != direction:
            print(f"  ⚠️ CONFLICT: Local tracking has {local_dir}, tried {direction}!")
        return {"success": False, "reason": "LOCAL_POSITION_EXISTS"}
    
    # Check minimum score
    if signal_score < AUTO_TRADE_MIN_SCORE:
        return {"success": False, "reason": f"SCORE_TOO_LOW ({signal_score} < {AUTO_TRADE_MIN_SCORE})"}
    
    try:
        # Calculate stop loss percentage for risk budget
        if direction == "LONG":
            stop_loss_pct = abs(price - sl) / price
        else:
            stop_loss_pct = abs(sl - price) / price
        
        # Determine optimal leverage based on signal confidence (v8.9)
        leverage_result = determine_leverage(
            signal_score=signal_score,
            ml_confidence=ml_confidence,
            confirmation_count=confirmation_count,
            stop_loss_pct=stop_loss_pct
        )
        
        selected_leverage = leverage_result['leverage']
        leverage_tier = leverage_result['tier']
        position_size_usd = leverage_result['position_size_usd']
        
        # v8.9.23: Apply SCALP_REBOUND size multiplier if active
        scalp_rebound_multiplier = kwargs.get('scalp_rebound_multiplier', 1.0)
        if scalp_rebound_multiplier < 1.0:
            original_size = position_size_usd
            position_size_usd = position_size_usd * scalp_rebound_multiplier
            print(f"     🔄 SCALP_REBOUND: Position reduced ${original_size:.0f} → ${position_size_usd:.0f} ({scalp_rebound_multiplier:.0%})")
        
        # Calculate position size in contracts
        position_size = position_size_usd / price
        
        # Determine order side
        side = "buy" if direction == "LONG" else "sell"
        
        # v8.9.6: Set leverage BEFORE placing order (Kraken uses account-level leverage)
        try:
            exchange.set_leverage(selected_leverage, symbol)
            print(f"     Leverage set to {selected_leverage}x for {symbol}")
        except Exception as lev_err:
            print(f"     ⚠️ Could not set leverage: {lev_err}")
        
        # v8.9.24: Place order based on entry_type (MARKET or LIMIT)
        if entry_type == "LIMIT":
            # LIMIT order - use specified price
            order = exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side,
                amount=position_size,
                price=price
            )
            print(f"     📋 LIMIT order placed @ ${price:.2f}")
        else:
            # MARKET order - execute immediately
            order = exchange.create_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=position_size
            )
            print(f"     ⚡ MARKET order executed")
        
        asset_name = ASSET_NAMES.get(symbol, symbol)
        
        # v8.9.21 #5: ORDER VERIFICATION - Verify order was filled
        order_id = order.get('id')
        order_status = order.get('status', 'unknown')
        filled_amount = order.get('filled', 0) or 0
        actual_entry_price = order.get('average', price) or price  # Get actual fill price
        
        # v8.9.24: Different verification logic for LIMIT vs MARKET orders
        if entry_type == "LIMIT":
            # LIMIT orders may take time to fill - track as pending
            if order_status in ['closed', 'filled']:
                # Already filled
                print(f"     ✅ LIMIT order filled immediately @ ${actual_entry_price:.2f}")
                if filled_amount > 0:
                    position_size = filled_amount
            elif order_status == 'open':
                # Order is open, waiting to fill - track it
                print(f"     ⏳ LIMIT order OPEN - waiting for fill @ ${price:.2f}")
                # Store order ID for tracking
                kwargs['limit_order_id'] = order_id
                kwargs['limit_order_pending'] = True
                # For now, we'll wait a bit and check again
                import time
                max_wait = 30  # Wait up to 30 seconds for limit order
                for i in range(6):  # Check every 5 seconds
                    time.sleep(5)
                    try:
                        order_check = exchange.fetch_order(order_id, symbol)
                        order_status = order_check.get('status', 'unknown')
                        filled_amount = order_check.get('filled', 0) or 0
                        actual_entry_price = order_check.get('average', price) or price
                        
                        if order_status in ['closed', 'filled']:
                            print(f"     ✅ LIMIT order filled @ ${actual_entry_price:.2f} after {(i+1)*5}s")
                            position_size = filled_amount
                            break
                        elif order_status == 'canceled':
                            return {"success": False, "reason": "LIMIT_ORDER_CANCELED"}
                        else:
                            print(f"     ⏳ Still waiting... ({(i+1)*5}s)")
                    except Exception as e:
                        print(f"     ⚠️ Check error: {e}")
                else:
                    # Timeout - cancel and abort
                    print(f"     ⏰ LIMIT order timeout after {max_wait}s - cancelling")
                    try:
                        exchange.cancel_order(order_id, symbol)
                    except:
                        pass
                    return {"success": False, "reason": "LIMIT_ORDER_TIMEOUT"}
            else:
                # Unknown status
                print(f"     ⚠️ LIMIT order status: {order_status}")
                return {"success": False, "reason": f"LIMIT_ORDER_STATUS: {order_status}"}
        else:
            # MARKET order verification (original logic)
            if order_status not in ['closed', 'filled'] or filled_amount < position_size * 0.95:
                # Order not fully filled - reconcile
                print(f"     ⚠️ ORDER VERIFICATION: Status={order_status}, Filled={filled_amount:.6f}/{position_size:.6f}")
                
                # Try to fetch order status
                try:
                    import time
                    time.sleep(1)  # Wait for exchange to process
                    order_check = exchange.fetch_order(order_id, symbol)
                    order_status = order_check.get('status', 'unknown')
                    filled_amount = order_check.get('filled', 0) or 0
                    actual_entry_price = order_check.get('average', price) or price
                    
                    if order_status not in ['closed', 'filled'] or filled_amount < position_size * 0.5:
                        # Order failed or partially filled - cancel and abort
                        print(f"     🔴 ORDER FAILED: Trying to cancel...")
                        try:
                            exchange.cancel_order(order_id, symbol)
                        except:
                            pass
                        return {"success": False, "reason": f"ORDER_NOT_FILLED: {order_status}"}
                    elif filled_amount < position_size * 0.95:
                        # Partial fill - adjust position size
                        print(f"     ⚠️ PARTIAL FILL: Using {filled_amount:.6f} instead of {position_size:.6f}")
                        position_size = filled_amount
                except Exception as verify_err:
                    print(f"     ⚠️ Could not verify order: {verify_err}")
        
        # Use actual entry price for position tracking
        if actual_entry_price and actual_entry_price != price:
            print(f"     📊 Actual entry: ${actual_entry_price:.2f} (signal: ${price:.2f})")
            price = actual_entry_price
        
        # v8.9.21: Place EXCHANGE-SIDE Stop Loss order immediately
        sl_order_id = None
        sl_side = "sell" if direction == "LONG" else "buy"
        try:
            # Kraken Futures requires 'stop' order type with triggerPrice
            sl_order = exchange.create_order(
                symbol=symbol,
                type='stop',
                side=sl_side,
                amount=position_size,
                price=sl,  # Trigger price for stop order
                params={
                    'triggerPrice': sl,
                    'reduceOnly': True
                }
            )
            sl_order_id = sl_order.get('id')
            print(f"     🛡️ EXCHANGE SL placed @ ${sl:.2f} (ID: {sl_order_id})")
        except Exception as sl_err:
            # Critical: If SL order fails, close position immediately
            print(f"     ⚠️ SL ORDER FAILED: {sl_err}")
            try:
                # Emergency close - SL is mandatory
                close_side = "sell" if direction == "LONG" else "buy"
                exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=close_side,
                    amount=position_size,
                    params={'reduceOnly': True}
                )
                print(f"     🚨 EMERGENCY CLOSE - Position closed due to SL failure")
                return {"success": False, "reason": "SL_ORDER_FAILED_EMERGENCY_CLOSE"}
            except Exception as close_err:
                print(f"     🔴 CRITICAL: Could not close position: {close_err}")
                # Send emergency alert
                try:
                    from telegram import Bot
                    tg_bot = Bot(token=TELEGRAM_TOKEN)
                    import asyncio
                    await tg_bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"🚨 CRITICAL ALERT\n\n{asset_name} {direction} pozicija BE STOP LOSS!\n\nPozicijos dydis: {position_size:.6f}\nĮėjimo kaina: ${price:.2f}\n\n⚠️ REIKALINGAS RANKINIS UŽDARYMAS!"
                    )
                except:
                    pass
                return {"success": False, "reason": "SL_ORDER_FAILED_POSITION_UNMANAGED"}
        
        # v8.9.24: Get context for Dynamic Exit Engine
        trade_mode = kwargs.get('trade_mode', 'NORMAL')
        atr_value = kwargs.get('atr', abs(price - sl))  # Fallback to SL distance as ATR proxy
        market_regime = kwargs.get('market_regime', 'BULL')
        trend_strength = kwargs.get('trend_strength', 'NORMAL')
        setup_type = kwargs.get('setup_type', 'CONTINUATION')
        volatility_level = kwargs.get('volatility_level', 'NORMAL')
        
        # v8.9.24: Calculate adaptive exit levels using Dynamic Exit Engine v2.0
        exit_ctx = ExitContext(
            trade_mode=trade_mode,
            entry_price=price,
            atr=atr_value,
            direction=direction,
            market_regime=market_regime,
            trend_strength=trend_strength,
            setup_type=setup_type,
            volatility_level=volatility_level
        )
        exit_levels = calculate_exit_levels(exit_ctx)
        
        # v8.9.24: Log Dynamic Exit Engine decision
        print(f"     🔄 EXIT ENGINE v2.0: {trade_mode} | {market_regime} | {trend_strength}")
        print(f"        TP: {exit_levels.tp1_percent}%/{exit_levels.tp2_percent}%/{exit_levels.tp3_percent}% | Runner: {exit_levels.runner_enabled}")
        print(f"        {exit_levels.tp_comment}")
        
        # v8.9.24: Override TP/SL with Exit Engine values
        if trade_mode == "SCALP_REBOUND" or exit_levels.runner_enabled:
            sl = exit_levels.stop_loss
            tp1 = exit_levels.tp1
            tp2 = exit_levels.tp2
            if exit_levels.tp3:
                tp3 = exit_levels.tp3
            print(f"        SL: ${sl:.2f} | TP1: ${tp1:.2f} | TP2: ${tp2:.2f} | TP3: ${exit_levels.tp3 or 'Runner'}")
            print(f"        Trailing: {exit_levels.trailing_distance:.2f} | MaxHold: {exit_levels.max_hold_minutes}min")
        
        # Track position for trailing stop management
        open_positions[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": price,
            "size": position_size,
            "original_size": position_size,
            "remaining_size": position_size,
            "sl": sl,
            "original_sl": sl,
            "current_sl": sl,
            "exchange_sl_order_id": sl_order_id,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp1_partial_closed": False,
            "tp2_partial_closed": False,
            "tp1_notified": False,
            "tp2_notified": False,
            "tp1_partial_notified": False,
            "tp2_partial_notified": False,
            "breakeven_notified": False,
            "trailing_active": False,
            "breakeven_active": False,
            "last_notified_sl": sl,
            "last_trailing_notify_time": None,
            "entry_time": datetime.now(timezone.utc),
            "from_auto_trade": True,
            "signal_score": signal_score,
            "leverage": selected_leverage,
            "leverage_tier": leverage_tier,
            "ml_confidence": ml_confidence,
            "confirmation_count": confirmation_count,
            "entry_type": entry_type,
            "trade_mode": trade_mode,
            "exit_profile": {
                "breakeven_after_tp1": exit_levels.breakeven_after_tp1,
                "trailing_enabled": exit_levels.trailing_enabled,
                "trailing_distance": exit_levels.trailing_distance,
                "runner_enabled": exit_levels.runner_enabled,
                "tp1_percent": exit_levels.tp1_percent,
                "tp2_percent": exit_levels.tp2_percent,
                "tp3_percent": exit_levels.tp3_percent,
                "max_hold_minutes": exit_levels.max_hold_minutes,
                "reason": exit_levels.reason,
                "tp_comment": exit_levels.tp_comment
            }
        }
        
        auto_trading_state['daily_trades'] += 1
        
        tier_emoji = {"STRONG": "💪", "MEDIUM": "✊", "WEAK": "👌", "MINIMAL": "☝️", "FIXED": "🔒"}.get(leverage_tier, "📊")
        print(f"  ✅ AUTO-TRADE EXECUTED: {asset_name} {direction} @ ${price:.2f}")
        print(f"     {tier_emoji} Leverage: {selected_leverage}x ({leverage_tier})")
        margin_usd = leverage_result.get('margin_usd', position_size_usd / selected_leverage)
        actual_risk = leverage_result.get('actual_risk_usd', position_size_usd * stop_loss_pct)
        sl_pct = leverage_result.get('sl_pct', stop_loss_pct)
        print(f"     Margin: ${margin_usd:.2f} | Position: ${position_size_usd:.2f} | Risk: ${actual_risk:.2f}")
        print(f"     SL: ${sl:.2f} ({sl_pct*100:.1f}%) | TP1: ${tp1:.2f} | TP2: ${tp2:.2f} | TP3: ${tp3:.2f}")
        
        return {
            "success": True,
            "order_id": order.get('id'),
            "symbol": symbol,
            "direction": direction,
            "size": position_size,
            "price": order.get('average') or price,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "leverage": selected_leverage,
            "leverage_tier": leverage_tier,
            "confirmation_count": confirmation_count,
        }
        
    except Exception as e:
        print(f"  ❌ AUTO-TRADE ERROR: {e}")
        return {"success": False, "reason": str(e)}

async def update_exchange_sl(symbol, new_sl_price, position_size):
    """
    v8.9.21: Update exchange-side stop loss order when trailing stop moves.
    
    Cancel old SL order and place new one at updated price.
    """
    global open_positions
    
    if symbol not in open_positions:
        return {"success": False, "reason": "NO_LOCAL_POSITION"}
    
    position = open_positions[symbol]
    old_sl_order_id = position.get('exchange_sl_order_id')
    direction = position['direction']
    remaining_size = position.get('remaining_size', position_size)
    
    try:
        # Cancel old SL order if exists
        if old_sl_order_id:
            try:
                exchange.cancel_order(old_sl_order_id, symbol)
                print(f"     🗑️ Old SL order cancelled (ID: {old_sl_order_id})")
            except Exception as cancel_err:
                print(f"     ⚠️ Could not cancel old SL: {cancel_err}")
        
        # Place new SL order
        sl_side = "sell" if direction == "LONG" else "buy"
        sl_order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=sl_side,
            amount=remaining_size,
            params={
                'stopPrice': new_sl_price,
                'reduceOnly': True
            }
        )
        
        new_sl_order_id = sl_order.get('id')
        position['exchange_sl_order_id'] = new_sl_order_id
        position['current_sl'] = new_sl_price
        
        print(f"     🛡️ Exchange SL updated to ${new_sl_price:.2f} (ID: {new_sl_order_id})")
        
        return {"success": True, "order_id": new_sl_order_id, "new_sl": new_sl_price}
        
    except Exception as e:
        print(f"     ⚠️ Failed to update exchange SL: {e}")
        return {"success": False, "reason": str(e)}

async def cancel_exchange_sl(symbol):
    """v8.9.21: Cancel exchange-side SL order when position is closed."""
    global open_positions
    
    if symbol not in open_positions:
        return {"success": False, "reason": "NO_LOCAL_POSITION"}
    
    position = open_positions[symbol]
    sl_order_id = position.get('exchange_sl_order_id')
    
    if not sl_order_id:
        return {"success": True, "reason": "NO_SL_ORDER"}
    
    try:
        exchange.cancel_order(sl_order_id, symbol)
        position['exchange_sl_order_id'] = None
        print(f"     🗑️ Exchange SL cancelled (ID: {sl_order_id})")
        return {"success": True}
    except Exception as e:
        print(f"     ⚠️ Could not cancel SL order: {e}")
        return {"success": False, "reason": str(e)}

async def close_full_position(symbol, reason="MANUAL"):
    """
    Close entire position on Kraken Futures.
    
    Args:
        symbol: Futures symbol (e.g. "PF_XBTUSD")
        reason: Why closing (SL_HIT, TP3_HIT, MANUAL, etc.)
    
    Returns:
        dict with success status and P&L
    """
    global auto_trading_state, open_positions
    
    if not POSITION_TRACKING_ENABLED:
        return {"success": False, "reason": "NO_API_KEYS"}
    
    try:
        # v8.9.21: Cancel exchange-side SL order first
        await cancel_exchange_sl(symbol)
        
        # Get current position from Kraken
        positions = exchange.fetch_positions()
        
        position_data = None
        for pos in positions:
            pos_symbol = pos.get('symbol', '')
            if symbol in pos_symbol or pos.get('info', {}).get('symbol') == symbol:
                if pos['contracts'] and float(pos['contracts']) > 0:
                    position_data = pos
                    break
        
        if not position_data:
            # Remove from local tracking if exists
            if symbol in open_positions:
                del open_positions[symbol]
            return {"success": False, "reason": "NO_POSITION_FOUND"}
        
        total_size = float(position_data['contracts'])
        pos_side = position_data['side']
        entry_price = float(position_data['entryPrice']) if position_data['entryPrice'] else 0
        unrealized_pnl = float(position_data['unrealizedPnl']) if position_data['unrealizedPnl'] else 0
        
        # Close order (opposite side)
        close_side = "sell" if pos_side == 'long' else "buy"
        direction = "LONG" if pos_side == 'long' else "SHORT"
        
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=close_side,
            amount=total_size,
            params={'reduceOnly': True}
        )
        
        close_price = order.get('average') or 0
        
        # Calculate realized P&L
        if direction == "LONG":
            pnl = (close_price - entry_price) * total_size
        else:
            pnl = (entry_price - close_price) * total_size
        
        # Update daily & weekly P&L (v8.9.18)
        auto_trading_state['daily_pnl'] += pnl
        auto_trading_state['weekly_pnl'] += pnl
        auto_trading_state['weekly_trades'] += 1
        if pnl > 0:
            auto_trading_state['daily_wins'] += 1
            auto_trading_state['weekly_wins'] += 1
        else:
            auto_trading_state['daily_losses'] += 1
            auto_trading_state['weekly_losses'] += 1
        
        # Remove from local tracking
        if symbol in open_positions:
            del open_positions[symbol]
        
        asset_name = ASSET_NAMES.get(symbol, symbol)
        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        print(f"  {pnl_emoji} POSITION CLOSED: {asset_name} {direction}")
        print(f"     Reason: {reason} | P&L: ${pnl:.2f}")
        
        return {
            "success": True,
            "order_id": order.get('id'),
            "symbol": symbol,
            "direction": direction,
            "close_price": close_price,
            "entry_price": entry_price,
            "pnl": pnl,
            "reason": reason,
        }
        
    except Exception as e:
        print(f"  ❌ CLOSE POSITION ERROR: {e}")
        return {"success": False, "reason": str(e)}

def reset_daily_stats():
    """Reset daily trading stats at midnight UTC"""
    global auto_trading_state
    
    now = datetime.now(timezone.utc)
    
    if auto_trading_state['last_reset'] is None:
        auto_trading_state['last_reset'] = now.date()
        auto_trading_state['last_weekly_reset'] = now.date()
        return
    
    # Daily reset
    if now.date() > auto_trading_state['last_reset']:
        print(f"📊 Daily Stats Reset - Yesterday: P&L=${auto_trading_state['daily_pnl']:.2f}, "
              f"Trades={auto_trading_state['daily_trades']}, "
              f"W/L={auto_trading_state['daily_wins']}/{auto_trading_state['daily_losses']}")
        
        auto_trading_state['daily_pnl'] = 0.0
        auto_trading_state['daily_trades'] = 0
        auto_trading_state['daily_wins'] = 0
        auto_trading_state['daily_losses'] = 0
        
        # Only reset pause if it was DAILY pause (not weekly)
        if auto_trading_state['pause_type'] == "DAILY":
            auto_trading_state['is_paused'] = False
            auto_trading_state['pause_reason'] = None
            auto_trading_state['pause_type'] = None
        
        auto_trading_state['last_reset'] = now.date()
    
    # Weekly reset (Monday UTC) - v8.9.18
    if auto_trading_state['last_weekly_reset'] is None:
        auto_trading_state['last_weekly_reset'] = now.date()
    
    # Check if it's a new week (Monday = 0)
    current_week = now.isocalendar()[1]
    last_reset_date = auto_trading_state['last_weekly_reset']
    if hasattr(last_reset_date, 'isocalendar'):
        last_week = last_reset_date.isocalendar()[1]
    else:
        last_week = current_week
    
    if current_week != last_week or (now.date() - last_reset_date).days >= 7:
        print(f"📊 Weekly Stats Reset - Last Week: P&L=${auto_trading_state['weekly_pnl']:.2f}, "
              f"Trades={auto_trading_state['weekly_trades']}, "
              f"W/L={auto_trading_state['weekly_wins']}/{auto_trading_state['weekly_losses']}")
        
        auto_trading_state['weekly_pnl'] = 0.0
        auto_trading_state['weekly_trades'] = 0
        auto_trading_state['weekly_wins'] = 0
        auto_trading_state['weekly_losses'] = 0
        
        # Reset weekly pause
        if auto_trading_state['pause_type'] == "WEEKLY":
            auto_trading_state['is_paused'] = False
            auto_trading_state['pause_reason'] = None
            auto_trading_state['pause_type'] = None
        
        auto_trading_state['last_weekly_reset'] = now.date()

# ================================
# KRAKEN POSITION TRACKING
# ================================
async def fetch_kraken_positions():
    """Fetch open positions from Kraken Futures API"""
    global kraken_positions
    
    if not POSITION_TRACKING_ENABLED:
        return {}
    
    now = datetime.now(timezone.utc)
    
    # Rate limit: only fetch every 30 seconds
    if kraken_positions['last_fetch']:
        elapsed = (now - kraken_positions['last_fetch']).total_seconds()
        if elapsed < kraken_positions['fetch_interval']:
            return kraken_positions['positions']
    
    try:
        # Fetch positions from Kraken Futures
        positions = exchange.fetch_positions()
        
        # Parse positions into our format
        new_positions = {}
        for pos in positions:
            if pos['contracts'] and float(pos['contracts']) != 0:
                symbol = pos['symbol']
                # Normalize symbol to match FUTURES_ASSETS format
                # ccxt returns like "BTC/USD:USD" -> we need "PF_XBTUSD"
                normalized = None
                if 'XBT' in symbol or 'BTC' in symbol:
                    normalized = "PF_XBTUSD"
                elif 'ETH' in symbol:
                    normalized = "PF_ETHUSD"
                elif 'SOL' in symbol:
                    normalized = "PF_SOLUSD"
                elif 'XRP' in symbol:
                    normalized = "PF_XRPUSD"
                elif 'LTC' in symbol:
                    normalized = "PF_LTCUSD"
                elif 'ADA' in symbol:
                    normalized = "PF_ADAUSD"
                elif 'DOT' in symbol:
                    normalized = "PF_DOTUSD"
                
                if normalized:
                    side = pos['side']  # 'long' or 'short'
                    direction = "LONG" if side == 'long' else "SHORT"
                    # v8.3: Get leverage from position data
                    leverage = pos.get('leverage', None)
                    if leverage is None:
                        # Try to calculate from margin info
                        notional = pos.get('notional', 0)
                        margin = pos.get('initialMargin', pos.get('margin', 0))
                        if margin and float(margin) > 0:
                            leverage = round(abs(float(notional)) / float(margin))
                        else:
                            leverage = LEVERAGE  # Default
                    else:
                        leverage = int(float(leverage))
                    
                    # v8.9.24: Real-time price data for live P&L
                    entry_price = float(pos['entryPrice']) if pos['entryPrice'] else 0
                    contracts = abs(float(pos['contracts']))
                    unrealized_pnl = float(pos.get('unrealizedPnl', 0)) if pos.get('unrealizedPnl') else 0
                    
                    # Try to get mark price from multiple sources
                    mark_price = None
                    if pos.get('markPrice'):
                        mark_price = float(pos['markPrice'])
                    elif pos.get('lastPrice'):
                        mark_price = float(pos['lastPrice'])
                    elif pos.get('info', {}).get('markPrice'):
                        mark_price = float(pos['info']['markPrice'])
                    
                    # If no mark price, calculate from unrealized PnL
                    if not mark_price and entry_price > 0 and contracts > 0:
                        # P&L = (mark - entry) * contracts for LONG
                        # mark = entry + (pnl / contracts)
                        if direction == "LONG":
                            mark_price = entry_price + (unrealized_pnl / contracts)
                        else:
                            mark_price = entry_price - (unrealized_pnl / contracts)
                    
                    if not mark_price:
                        mark_price = entry_price
                    
                    # Calculate notional value (contracts × entry price)
                    notional_from_api = pos.get('notional')
                    if notional_from_api and float(notional_from_api) != 0:
                        notional_value = abs(float(notional_from_api))
                    else:
                        # Calculate manually if API doesn't provide it
                        notional_value = contracts * entry_price
                    
                    # Get actual margin from Kraken (check multiple sources including info dict)
                    initial_margin = None
                    
                    # Check top-level fields first
                    for field in ['initialMargin', 'margin', 'collateral']:
                        if pos.get(field) and float(pos.get(field)) > 0:
                            initial_margin = abs(float(pos.get(field)))
                            break
                    
                    # Check info dict (Kraken-specific fields)
                    if not initial_margin and pos.get('info'):
                        info = pos['info']
                        for field in ['initialMargin', 'margin', 'marginUsed', 'im']:
                            if info.get(field) and float(info.get(field)) > 0:
                                initial_margin = abs(float(info.get(field)))
                                break
                    
                    # Calculate size_usd (margin used)
                    if initial_margin and initial_margin > 0:
                        size_usd = initial_margin
                    elif leverage > 0:
                        # Margin = Notional / Leverage
                        size_usd = notional_value / leverage
                    else:
                        # Unknown leverage - use notional as fallback (will be inaccurate for isolated margin)
                        size_usd = notional_value
                    
                    new_positions[normalized] = {
                        "direction": direction,
                        "size": contracts,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "size_usd": size_usd,
                        "unrealized_pnl": unrealized_pnl,
                        "leverage": leverage,
                    }
        
        kraken_positions['positions'] = new_positions
        kraken_positions['last_fetch'] = now
        
        if new_positions:
            pos_list = [f"{ASSET_NAMES.get(s, s)} {p['direction']} {p['leverage']}x" for s, p in new_positions.items()]
            print(f"📍 Kraken positions: {', '.join(pos_list)}")
        
        return new_positions
        
    except Exception as e:
        print(f"⚠️ Error fetching Kraken positions: {e}")
        return kraken_positions['positions']  # Return cached positions

def has_open_position(symbol, direction=None):
    """Check if there's an open position for this symbol"""
    pos = kraken_positions['positions'].get(symbol)
    if not pos:
        return False
    if direction:
        return pos['direction'] == direction
    return True

async def load_existing_positions_for_trailing():
    """Load existing Kraken positions into trailing stop tracker on startup"""
    global open_positions
    
    if not TRAILING_ENABLED or not POSITION_TRACKING_ENABLED:
        return
    
    print("\n📍 Loading existing positions for trailing stop management...")
    
    await fetch_kraken_positions()
    
    for symbol, pos in kraken_positions['positions'].items():
        if symbol in open_positions:
            continue
            
        try:
            df = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, limit=50)
            df_1h = await fetch_ohlcv(symbol, TIMEFRAME_TREND, limit=50)
            if df is None or len(df) < 20:
                continue
            
            current_price = pos['entry_price']
            direction = pos['direction']
            
            from ta.volatility import AverageTrueRange
            atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range().iloc[-1]
            
            vix_value = macro_state.get('vix_value', 20)
            atr_mult = get_volatility_adjusted_atr_multiplier(df_1h, vix_value)
            
            # v8.7: TP1 pagal S/R zonas
            tp1, tp1_used_sr = get_tp1_from_sr_zone(current_price, direction, df, atr)
            
            if direction == "LONG":
                sl = current_price - (atr * atr_mult)
                tp2 = current_price + (atr * 2.5)
                tp3 = current_price + (atr * 4.0)
            else:
                sl = current_price + (atr * atr_mult)
                tp2 = current_price - (atr * 2.5)
                tp3 = current_price - (atr * 4.0)
            
            open_positions[symbol] = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": current_price,
                "size": pos['size'],
                "original_size": pos['size'],
                "current_sl": sl,
                "original_sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "atr": atr,
                "trailing_active": False,
                "breakeven_active": False,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp1_partial_closed": False,
                "tp2_partial_closed": False,
                "highest_price": current_price if direction == "LONG" else None,
                "lowest_price": current_price if direction == "SHORT" else None,
                "open_time": datetime.now(timezone.utc),
                "last_update": datetime.now(timezone.utc),
                "last_notified_sl": sl,
                "from_kraken": True,
            }
            
            asset_name = ASSET_NAMES.get(symbol, symbol)
            print(f"  ✅ {asset_name} {direction} @ ${current_price:.2f}")
            print(f"     SL: ${sl:.2f} | TP1: ${tp1:.2f} | TP2: ${tp2:.2f} | TP3: ${tp3:.2f}")
            
            if TELEGRAM_TOKEN and CHAT_ID:
                msg = f"""
🔄 <b>TRAILING STOP ACTIVATED</b> 🔄

📊 <b>{asset_name}</b> | {direction}
💰 Entry: ${current_price:,.2f}

🎯 Targets:
  TP1: ${tp1:,.2f}
  TP2: ${tp2:,.2f}
  TP3: ${tp3:,.2f}

🛡️ Stop Loss: ${sl:,.2f}
📈 Trailing: {TRAILING_DISTANCE_PCT}% distance
✅ Breakeven at TP1: {"YES" if BREAKEVEN_AT_TP1 else "NO"}

<i>Trailing stop now managing this position</i>
"""
                try:
                    bot = Bot(token=TELEGRAM_TOKEN)
                    await bot.send_message(chat_id=CHAT_ID, text=msg.strip(), parse_mode='HTML')
                except Exception as e:
                    print(f"  ⚠️ Telegram error: {e}")
                    
        except Exception as e:
            print(f"  ⚠️ Error loading {symbol}: {e}")
    
    if open_positions:
        print(f"\n✅ Loaded {len(open_positions)} position(s) for trailing stop management")
    else:
        print("  ℹ️ No open positions found on Kraken")

# Position sync interval (60 seconds for faster detection)
POSITION_SYNC_INTERVAL = 60  # seconds
last_position_sync = None

async def sync_positions_with_kraken():
    """
    Periodically sync local open_positions with actual Kraken positions.
    - Removes positions that were closed manually on Kraken.
    - Adds and manages positions that were opened manually on Kraken.
    """
    global open_positions, last_position_sync
    
    now = datetime.now(timezone.utc)
    
    # Rate limit sync
    if last_position_sync and (now - last_position_sync).total_seconds() < POSITION_SYNC_INTERVAL:
        return
    
    last_position_sync = now
    
    # Force fresh fetch from Kraken
    kraken_positions['last_fetch'] = None
    await fetch_kraken_positions()
    
    kraken_symbols = set(kraken_positions['positions'].keys())
    local_symbols = set(open_positions.keys())
    
    # Find positions that are in local tracker but NOT on Kraken (manually closed)
    closed_symbols = local_symbols - kraken_symbols
    
    # Symbol mapping for ticker fetch
    TICKER_SYMBOLS = {
        "PF_XBTUSD": "BTC/USD:USD",
        "PF_ETHUSD": "ETH/USD:USD",
        "PF_SOLUSD": "SOL/USD:USD",
        "PF_XRPUSD": "XRP/USD:USD",
        "PF_LTCUSD": "LTC/USD:USD",
        "PF_ADAUSD": "ADA/USD:USD",
        "PF_DOTUSD": "DOT/USD:USD",
    }
    
    if closed_symbols:
        for symbol in closed_symbols:
            pos = open_positions.pop(symbol, None)
            if pos:
                asset_name = ASSET_NAMES.get(symbol, symbol)
                entry_price = pos.get('entry_price', 0)
                direction = pos.get('direction', 'LONG')
                size_usd = pos.get('size_usd', 25)
                
                # Fetch current price to determine WIN/LOSS
                try:
                    ticker_symbol = TICKER_SYMBOLS.get(symbol)
                    if ticker_symbol:
                        ticker = exchange.fetch_ticker(ticker_symbol)
                        current_price = ticker.get('last', entry_price) or entry_price
                    else:
                        current_price = entry_price
                except:
                    current_price = entry_price
                
                # Calculate P&L
                if entry_price > 0:
                    if direction == "LONG":
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - current_price) / entry_price * 100
                else:
                    pnl_pct = 0
                
                # Determine WIN or LOSS
                is_win = pnl_pct > 0
                result = "WIN" if is_win else "LOSS"
                result_emoji = "✅" if is_win else "❌"
                pnl_sign = "+" if pnl_pct > 0 else ""
                
                # Update stats
                if is_win:
                    bot_stats["wins"] += 1
                else:
                    bot_stats["losses"] += 1
                
                # Calculate P&L in USD and update risk limits (v8.9.23 fix)
                contracts = pos.get('size', 0)
                if contracts > 0 and entry_price > 0:
                    if direction == "LONG":
                        pnl_usd = (current_price - entry_price) * contracts
                    else:
                        pnl_usd = (entry_price - current_price) * contracts
                else:
                    # Fallback: estimate from percentage and margin
                    pnl_usd = size_usd * (pnl_pct / 100)
                
                auto_trading_state['daily_pnl'] += pnl_usd
                auto_trading_state['weekly_pnl'] += pnl_usd
                auto_trading_state['weekly_trades'] += 1
                if is_win:
                    auto_trading_state['daily_wins'] += 1
                    auto_trading_state['weekly_wins'] += 1
                else:
                    auto_trading_state['daily_losses'] += 1
                    auto_trading_state['weekly_losses'] += 1
                
                print(f"  💰 P&L: ${pnl_usd:+.2f} | Daily: ${auto_trading_state['daily_pnl']:.2f}")
                
                # Record to trade results file
                record_trade_result(symbol, direction, result, pnl_pct, pos.get('is_counter_trend', False))
                
                print(f"🔄 SYNC: {asset_name} {direction} closed on Kraken - {result} ({pnl_sign}{pnl_pct:.2f}%)")
                
                # Notify via Telegram
                if TELEGRAM_TOKEN and CHAT_ID:
                    try:
                        msg = f"""
🔄 <b>POSITION CLOSED</b>

📊 <b>{asset_name}</b> {direction}
{result_emoji} Rezultatas: <b>{result}</b>
💰 P&L: {pnl_sign}{pnl_pct:.2f}%
📍 Įėjimas: ${entry_price:,.2f}
📍 Išėjimas: ~${current_price:,.2f}

<i>Pozicija uždaryta per Kraken TP/SL arba rankiniu būdu</i>
"""
                        bot = Bot(token=TELEGRAM_TOKEN)
                        await bot.send_message(chat_id=CHAT_ID, text=msg.strip(), parse_mode='HTML')
                    except Exception as e:
                        print(f"  ⚠️ Telegram error: {e}")
        
        print(f"🔄 Position sync complete: {len(closed_symbols)} position(s) removed")
    
    # Add and manage positions that were opened manually on Kraken
    new_symbols = kraken_symbols - local_symbols
    if new_symbols:
        from ta.volatility import AverageTrueRange
        
        for symbol in new_symbols:
            try:
                kraken_pos = kraken_positions['positions'][symbol]
                direction = kraken_pos['direction']
                entry_price = kraken_pos['entry_price']
                size = kraken_pos['size']
                leverage = kraken_pos.get('leverage', LEVERAGE)
                
                # Fetch OHLCV data for TP/SL calculation
                df = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, limit=50)
                df_1h = await fetch_ohlcv(symbol, TIMEFRAME_TREND, limit=50)
                
                if df is None or len(df) < 20:
                    print(f"  ⚠️ Cannot load {symbol}: insufficient data")
                    continue
                
                # Calculate ATR and targets
                atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range().iloc[-1]
                vix_value = macro_state.get('vix_value', 20)
                atr_mult = get_volatility_adjusted_atr_multiplier(df_1h, vix_value)
                
                # v8.7: TP1 based on S/R zones
                tp1, tp1_used_sr = get_tp1_from_sr_zone(entry_price, direction, df, atr)
                
                if direction == "LONG":
                    sl = entry_price - (atr * atr_mult)
                    tp2 = entry_price + (atr * 2.5)
                    tp3 = entry_price + (atr * 4.0)
                else:
                    sl = entry_price + (atr * atr_mult)
                    tp2 = entry_price - (atr * 2.5)
                    tp3 = entry_price - (atr * 4.0)
                
                # Get current price
                current_price = df['close'].iloc[-1]
                
                # Create position tracking
                open_positions[symbol] = {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_price,
                    "size": size,
                    "original_size": size,
                    "current_sl": sl,
                    "original_sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "atr": atr,
                    "trailing_active": False,
                    "breakeven_active": False,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "tp1_partial_closed": False,
                    "tp2_partial_closed": False,
                    "highest_price": current_price if direction == "LONG" else None,
                    "lowest_price": current_price if direction == "SHORT" else None,
                    "open_time": datetime.now(timezone.utc),
                    "last_update": datetime.now(timezone.utc),
                    "last_notified_sl": sl,
                    "from_kraken": True,
                    "manual_entry": True,
                }
                
                asset_name = ASSET_NAMES.get(symbol, symbol)
                pnl = kraken_pos.get('unrealized_pnl', 0)
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if direction == "LONG" else ((entry_price - current_price) / entry_price * 100)
                
                print(f"🔄 SYNC: {asset_name} {direction} {leverage}x @ ${entry_price:.2f} - MANAGING NOW")
                print(f"     Current: ${current_price:.2f} | P&L: {pnl_pct:+.2f}%")
                print(f"     SL: ${sl:.2f} | TP1: ${tp1:.2f} | TP2: ${tp2:.2f} | TP3: ${tp3:.2f}")
                
                # Notify via Telegram
                if TELEGRAM_TOKEN and CHAT_ID:
                    try:
                        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                        msg = f"""
🔄 <b>RANKINĖ POZICIJA PERIMTA</b> 🔄

📊 <b>{asset_name}</b> | {direction} {leverage}x
💰 Entry: ${entry_price:,.2f}
📈 Dabartinė: ${current_price:,.2f}
{pnl_emoji} P&L: {pnl_pct:+.2f}%

🎯 <b>Targets (automatiniai):</b>
  TP1: ${tp1:,.2f}
  TP2: ${tp2:,.2f}
  TP3: ${tp3:,.2f}

🛡️ Stop Loss: ${sl:,.2f}
📈 Trailing: {TRAILING_DISTANCE_PCT}% distance
✅ Breakeven at TP1: {"YES" if BREAKEVEN_AT_TP1 else "NO"}
🤖 Partial TP: 33% at TP1 & TP2

<i>Botas dabar valdo šią poziciją</i>
"""
                        bot = Bot(token=TELEGRAM_TOKEN)
                        await bot.send_message(chat_id=CHAT_ID, text=msg.strip(), parse_mode='HTML')
                    except Exception as e:
                        print(f"  ⚠️ Telegram error: {e}")
                        
            except Exception as e:
                print(f"  ⚠️ Error loading {symbol}: {e}")

# ================================
# TRAILING STOP FUNCTIONS
# ================================
def create_position(signal, signal_id=None, size=0):
    """Create a tracked position from a signal"""
    return {
        "symbol": signal['symbol'],
        "direction": signal['direction'],
        "entry_price": signal['price'],
        "size": size,
        "original_size": size,
        "current_sl": signal['sl'],
        "original_sl": signal['sl'],
        "tp1": signal['tp1'],
        "tp2": signal['tp2'],
        "tp3": signal['tp3'],
        "atr": signal['atr'],
        "trailing_active": False,
        "breakeven_active": False,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp1_partial_closed": False,
        "tp2_partial_closed": False,
        "highest_price": signal['price'] if signal['direction'] == "LONG" else None,
        "lowest_price": signal['price'] if signal['direction'] == "SHORT" else None,
        "open_time": datetime.now(timezone.utc),
        "last_update": datetime.now(timezone.utc),
        "signal_id": signal_id,
    }

def auto_mark_signal_result(position, close_reason, current_price):
    """Automatiškai pažymėti signalo rezultatą kai pozicija uždaroma"""
    signal_id = position.get('signal_id')
    if not signal_id:
        # Bandyti rasti signalą pagal simbolį ir laiką
        data = load_signal_results()
        for sig in reversed(data.get("signals", [])):
            if sig["symbol"] == position["symbol"] and sig["direction"] == position["direction"]:
                if sig.get("result") is None:
                    signal_id = sig["id"]
                    break
    
    if not signal_id:
        print(f"  → Auto-mark: Signal ID not found for {position['symbol']}")
        return
    
    entry = position['entry_price']
    direction = position['direction']
    
    if direction == "LONG":
        profit_pct = ((current_price - entry) / entry) * 100
    else:
        profit_pct = ((entry - current_price) / entry) * 100
    
    if close_reason in ["TP1_HIT", "TP2_HIT", "TP3_HIT"]:
        result = "WIN"
    elif close_reason == "SL_HIT":
        result = "LOSS"
    else:
        result = "WIN" if profit_pct > 0 else "LOSS"
    
    mark_signal_result(signal_id, result, profit_pct)
    print(f"  → Auto-marked: {signal_id} = {result} ({profit_pct:+.2f}%)")

def calculate_trailing_sl(position, current_price):
    """Calculate new trailing stop level"""
    direction = position['direction']
    trailing_distance = current_price * (TRAILING_DISTANCE_PCT / 100)
    
    if direction == "LONG":
        new_sl = current_price - trailing_distance
        if new_sl > position['current_sl']:
            return new_sl
    else:  # SHORT
        new_sl = current_price + trailing_distance
        if new_sl < position['current_sl']:
            return new_sl
    
    return None  # No update needed

def check_position_status(position, current_price):
    """
    Check position status and return updates.
    Returns dict with: tp1_hit, tp2_hit, tp3_hit, sl_hit, trailing_update, breakeven_update
    """
    updates = {
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "sl_hit": False,
        "trailing_update": None,
        "breakeven_update": False,
        "closed": False,
        "close_reason": None,
    }
    
    direction = position['direction']
    entry = position['entry_price']
    
    if direction == "LONG":
        profit_pct = ((current_price - entry) / entry) * 100
        
        # Update highest price
        if current_price > (position.get('highest_price') or entry):
            position['highest_price'] = current_price
        
        # Check TP hits
        if not position['tp1_hit'] and current_price >= position['tp1']:
            updates['tp1_hit'] = True
            position['tp1_hit'] = True
            
            # Activate breakeven
            if BREAKEVEN_AT_TP1 and not position['breakeven_active']:
                position['current_sl'] = entry
                position['breakeven_active'] = True
                updates['breakeven_update'] = True
        
        if not position['tp2_hit'] and current_price >= position['tp2']:
            updates['tp2_hit'] = True
            position['tp2_hit'] = True
        
        if current_price >= position['tp3']:
            updates['tp3_hit'] = True
            updates['closed'] = True
            updates['close_reason'] = "TP3_HIT"
        
        # Check SL hit
        if current_price <= position['current_sl']:
            updates['sl_hit'] = True
            updates['closed'] = True
            updates['close_reason'] = "SL_HIT"
        
        # Check trailing activation and update
        if TRAILING_ENABLED and profit_pct >= TRAILING_ACTIVATION_PCT:
            if not position['trailing_active']:
                position['trailing_active'] = True
            
            new_sl = calculate_trailing_sl(position, current_price)
            if new_sl:
                updates['trailing_update'] = new_sl
                position['current_sl'] = new_sl
                
    else:  # SHORT
        profit_pct = ((entry - current_price) / entry) * 100
        
        # Update lowest price
        if current_price < (position.get('lowest_price') or entry):
            position['lowest_price'] = current_price
        
        # Check TP hits
        if not position['tp1_hit'] and current_price <= position['tp1']:
            updates['tp1_hit'] = True
            position['tp1_hit'] = True
            
            # Activate breakeven
            if BREAKEVEN_AT_TP1 and not position['breakeven_active']:
                position['current_sl'] = entry
                position['breakeven_active'] = True
                updates['breakeven_update'] = True
        
        if not position['tp2_hit'] and current_price <= position['tp2']:
            updates['tp2_hit'] = True
            position['tp2_hit'] = True
        
        if current_price <= position['tp3']:
            updates['tp3_hit'] = True
            updates['closed'] = True
            updates['close_reason'] = "TP3_HIT"
        
        # Check SL hit
        if current_price >= position['current_sl']:
            updates['sl_hit'] = True
            updates['closed'] = True
            updates['close_reason'] = "SL_HIT"
        
        # Check trailing activation and update
        if TRAILING_ENABLED and profit_pct >= TRAILING_ACTIVATION_PCT:
            if not position['trailing_active']:
                position['trailing_active'] = True
            
            new_sl = calculate_trailing_sl(position, current_price)
            if new_sl:
                updates['trailing_update'] = new_sl
                position['current_sl'] = new_sl
    
    position['last_update'] = datetime.now(timezone.utc)
    return updates

# ================================
# PARTIAL POSITION CLOSE (v8.4)
# ================================
async def close_partial_position(symbol, direction, close_pct, current_price, reason="TP", original_size=None):
    """
    Close a partial position using Kraken Futures reduce-only order.
    
    Args:
        symbol: Futures symbol (e.g. "PF_XBTUSD")
        direction: Original position direction ("LONG" or "SHORT")
        close_pct: Percentage of ORIGINAL position to close (0.33 = 33%)
        current_price: Current market price
        reason: Reason for close ("TP1" or "TP2")
        original_size: Original position size (to calculate correct partial close size)
    
    Returns:
        dict with success status, closed_size, and order details
    """
    if not PARTIAL_TP_ENABLED:
        return {"success": False, "reason": "PARTIAL_TP_DISABLED"}
    
    if not POSITION_TRACKING_ENABLED:
        return {"success": False, "reason": "NO_API_KEYS"}
    
    try:
        positions = exchange.fetch_positions()
        
        position_data = None
        for pos in positions:
            if pos['symbol'] == symbol or pos['info'].get('symbol') == symbol:
                if pos['contracts'] and float(pos['contracts']) > 0:
                    position_data = pos
                    break
        
        if not position_data:
            return {"success": False, "reason": "NO_POSITION_FOUND"}
        
        total_size = float(position_data['contracts'])
        pos_side = position_data['side']
        
        if (direction == "LONG" and pos_side != 'long') or (direction == "SHORT" and pos_side != 'short'):
            return {"success": False, "reason": "DIRECTION_MISMATCH"}
        
        base_size = original_size if original_size else total_size
        close_size = base_size * close_pct
        
        if close_size > total_size:
            close_size = total_size * 0.9
        
        close_value_usd = close_size * current_price
        if close_value_usd < PARTIAL_MIN_SIZE_USD:
            return {"success": False, "reason": "SIZE_TOO_SMALL", "size_usd": close_value_usd}
        
        close_side = "sell" if direction == "LONG" else "buy"
        
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=close_side,
            amount=close_size,
            params={'reduceOnly': True}
        )
        
        asset_name = ASSET_NAMES.get(symbol, symbol)
        print(f"  ✅ Partial close executed: {asset_name} {direction} - {close_pct*100:.0f}% ({close_size:.6f})")
        
        remaining_size = total_size - close_size
        
        # v8.9.21: Update exchange SL with new remaining size
        if symbol in open_positions and remaining_size > 0:
            position = open_positions[symbol]
            current_sl = position.get('current_sl', position.get('sl'))
            if position.get('exchange_sl_order_id') and current_sl:
                await update_exchange_sl(symbol, current_sl, remaining_size)
        
        return {
            "success": True,
            "closed_size": close_size,
            "closed_pct": close_pct,
            "remaining_size": remaining_size,
            "order_id": order.get('id'),
            "price": order.get('average') or current_price,
            "reason": reason
        }
        
    except Exception as e:
        print(f"  ❌ Partial close error: {e}")
        return {"success": False, "reason": str(e)}

# ================================
# MARKET REGIME DETECTION
# ================================
def calculate_bb_squeeze(df):
    """Calculate Bollinger Band Squeeze (low volatility indicator)."""
    if df is None or len(df) < 20:
        return False, 0
    
    close = df['close']
    bb = BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_width = (bb_upper - bb_lower) / close
    
    # Keltner Channel for squeeze detection
    atr = AverageTrueRange(df['high'], df['low'], close, window=20).average_true_range()
    ema20 = EMAIndicator(close, window=20).ema_indicator()
    kc_upper = ema20 + (atr * 1.5)
    kc_lower = ema20 - (atr * 1.5)
    
    # Squeeze = BB inside KC
    is_squeeze = (bb_lower.iloc[-1] > kc_lower.iloc[-1]) and (bb_upper.iloc[-1] < kc_upper.iloc[-1])
    squeeze_strength = 1 - (bb_width.iloc[-1] / bb_width.rolling(50).mean().iloc[-1]) if bb_width.rolling(50).mean().iloc[-1] > 0 else 0
    
    return is_squeeze, squeeze_strength

def detect_rsi_divergence(df: pd.DataFrame, direction: str = "LONG", lookback: int = 20) -> bool:
    """Detect RSI divergence - bullish or bearish"""
    if df is None or len(df) < lookback + 5:
        return False
    
    try:
        # Calculate RSI
        rsi_indicator = RSIIndicator(close=df['close'], window=14)
        rsi_values = rsi_indicator.rsi().values
        
        # Get price and RSI for lookback period
        prices = df['close'].values[-lookback:]
        rsi = rsi_values[-lookback:]
        
        if len(prices) < 10 or len(rsi) < 10:
            return False
        
        # Find recent price peaks/troughs
        if direction == "LONG":
            # Bullish divergence: price makes lower low, RSI makes higher low
            # Find last two significant lows
            recent_prices = prices[-10:]
            recent_rsi = rsi[-10:]
            
            # Find local minima
            min_idx_1 = np.argmin(recent_prices[:5])  # First half
            min_idx_2 = 5 + np.argmin(recent_prices[5:])  # Second half
            
            if min_idx_2 > min_idx_1 and min_idx_2 < len(recent_prices) - 1:
                price_low_1 = recent_prices[min_idx_1]
                price_low_2 = recent_prices[min_idx_2]
                rsi_low_1 = recent_rsi[min_idx_1]
                rsi_low_2 = recent_rsi[min_idx_2]
                
                # Bullish divergence: price lower, RSI higher
                if price_low_2 < price_low_1 and rsi_low_2 > rsi_low_1:
                    # Check if difference is significant
                    price_diff = (price_low_1 - price_low_2) / price_low_1
                    rsi_diff = rsi_low_2 - rsi_low_1
                    if price_diff > 0.005 and rsi_diff > 2:  # At least 0.5% price drop, 2 RSI points up
                        return True
        
        elif direction == "SHORT":
            # Bearish divergence: price makes higher high, RSI makes lower high
            recent_prices = prices[-10:]
            recent_rsi = rsi[-10:]
            
            # Find local maxima
            max_idx_1 = np.argmax(recent_prices[:5])
            max_idx_2 = 5 + np.argmax(recent_prices[5:])
            
            if max_idx_2 > max_idx_1 and max_idx_2 < len(recent_prices) - 1:
                price_high_1 = recent_prices[max_idx_1]
                price_high_2 = recent_prices[max_idx_2]
                rsi_high_1 = recent_rsi[max_idx_1]
                rsi_high_2 = recent_rsi[max_idx_2]
                
                # Bearish divergence: price higher, RSI lower
                if price_high_2 > price_high_1 and rsi_high_2 < rsi_high_1:
                    price_diff = (price_high_2 - price_high_1) / price_high_1
                    rsi_diff = rsi_high_1 - rsi_high_2
                    if price_diff > 0.005 and rsi_diff > 2:
                        return True
        
        return False
    except Exception as e:
        print(f"RSI divergence detection error: {e}")
        return False

def detect_market_regime():
    """
    Detect market regime based on BTC trend vs 200 EMA + ADX + BB Squeeze.
    v8.9.23: Uses Market Regime Engine v1.0 for BULL/BEAR/RANGE detection.
    """
    global market_regime_state
    
    if not MARKET_REGIME_ENABLED:
        return "NEUTRAL"
    
    try:
        ohlcv = exchange.fetch_ohlcv("PF_XBTUSD", "1d", limit=250)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        # Calculate indicators
        ema50 = EMAIndicator(df['close'], window=50).ema_indicator()
        ema200 = EMAIndicator(df['close'], window=200).ema_indicator()
        current_price = df['close'].iloc[-1]
        ema50_value = ema50.iloc[-1]
        ema200_value = ema200.iloc[-1]
        
        btc_vs_ema = (current_price - ema200_value) / ema200_value
        
        # EMA slope (5-period change)
        ema50_slope = (ema50.iloc[-1] - ema50.iloc[-5]) / ema50.iloc[-5] * 100 if len(ema50) >= 5 else 0
        
        # ADX for trend strength
        adx_indicator = ADXIndicator(df['high'], df['low'], df['close'], window=14)
        adx_value = adx_indicator.adx().iloc[-1]
        strong_trend = adx_value > 25
        
        # RSI
        rsi_indicator = RSIIndicator(df['close'], window=14)
        rsi_value = rsi_indicator.rsi().iloc[-1]
        
        # ATR ratio
        atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        atr_current = atr.iloc[-1]
        atr_avg = atr.rolling(50).mean().iloc[-1] if len(atr) >= 50 else atr_current
        atr_ratio = atr_current / atr_avg if atr_avg > 0 else 1.0
        
        # VWAP distance (approximate using typical price)
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap_approx = (typical_price * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()
        vwap_value = vwap_approx.iloc[-1] if not pd.isna(vwap_approx.iloc[-1]) else current_price
        vwap_distance = (current_price - vwap_value) / vwap_value * 100
        
        # BB Squeeze detection
        is_squeeze, squeeze_strength = calculate_bb_squeeze(df)
        market_regime_state["bb_squeeze"] = is_squeeze
        market_regime_state["squeeze_strength"] = squeeze_strength
        market_regime_state["adx"] = adx_value
        
        # v8.9.23: Use Market Regime Engine v2 for multi-factor detection
        regime_ctx = RegimeContext(
            ema_fast=ema50_value,
            ema_slow=ema200_value,
            ema_slope=ema50_slope,
            atr_ratio=atr_ratio,
            rsi=rsi_value,
            adx=adx_value,
            vwap_distance=vwap_distance
        )
        regime_result = detect_regime_v2(regime_ctx)
        
        # Map v2 regime to our system with strength
        v2_regime = regime_result.regime
        v2_confidence = regime_result.confidence
        
        # Combine legacy logic with v2 engine
        # v8.9.23: Full AUTO-REGIME SWITCH implementation
        shorts_blocked = False
        allow_range_scalps = False
        
        if v2_regime == "BULL":
            regime = "STRONG_BULL" if (strong_trend and v2_confidence >= 60) else "BULL"
            longs_blocked = False
            shorts_blocked = True  # Block SHORTs in BULL market
        elif v2_regime == "BEAR":
            regime = "STRONG_BEAR" if (strong_trend and v2_confidence >= 60) else "BEAR"
            longs_blocked = DEFENSIVE_MODE_ENABLED
            shorts_blocked = False  # Allow SHORTs in BEAR market
        elif v2_regime == "RANGE":
            if is_squeeze:
                regime = "SQUEEZE"
            else:
                regime = "RANGE"  # v8.9.23: New RANGE regime!
            longs_blocked = True   # Block trend LONGs in RANGE
            shorts_blocked = True  # Block trend SHORTs in RANGE
            allow_range_scalps = True  # Only range scalps allowed
        else:
            regime = "NEUTRAL"
            longs_blocked = False
            shorts_blocked = False
        
        market_regime_state["regime"] = regime
        market_regime_state["regime_v2"] = v2_regime
        market_regime_state["regime_confidence"] = v2_confidence
        market_regime_state["btc_vs_ema200"] = btc_vs_ema
        market_regime_state["longs_blocked"] = longs_blocked
        market_regime_state["shorts_blocked"] = shorts_blocked
        market_regime_state["allow_range_scalps"] = allow_range_scalps
        market_regime_state["defensive_mode"] = longs_blocked
        market_regime_state["last_check"] = datetime.now(timezone.utc)
        market_regime_state["atr_ratio"] = atr_ratio
        market_regime_state["rsi"] = rsi_value
        market_regime_state["vwap_distance"] = vwap_distance
        
        squeeze_msg = " | 🔥 SQUEEZE" if is_squeeze else ""
        confidence_msg = f" | Confidence: {v2_confidence:.0f}%"
        if "BEAR" in regime:
            market_regime_state["regime_message"] = f"🐻 {regime} - BTC {btc_vs_ema*100:.1f}% vs EMA200 | ADX: {adx_value:.0f}{squeeze_msg}{confidence_msg}"
        elif "BULL" in regime:
            market_regime_state["regime_message"] = f"🐂 {regime} - BTC +{btc_vs_ema*100:.1f}% vs EMA200 | ADX: {adx_value:.0f}{squeeze_msg}{confidence_msg}"
        elif regime == "SQUEEZE":
            market_regime_state["regime_message"] = f"🔥 SQUEEZE - Low volatility, breakout expected | ADX: {adx_value:.0f}{confidence_msg}"
        elif regime == "RANGE":
            market_regime_state["regime_message"] = f"📊 RANGE - Sideways market, trade edges only | ADX: {adx_value:.0f}{confidence_msg}"
        else:
            market_regime_state["regime_message"] = f"⚖️ NEUTRAL - BTC near 200 EMA | ADX: {adx_value:.0f}{squeeze_msg}{confidence_msg}"
        
        return regime
        
    except Exception as e:
        print(f"Regime detection error: {e}")
        return market_regime_state.get("regime", "NEUTRAL")

# ================================
# S&P 500 CORRELATION (via Finnhub)
# ================================
def get_spy_data():
    """Get S&P 500 (SPY ETF) data from Finnhub API."""
    global spy_state
    
    if not SPY_ENABLED or not FINNHUB_API_KEY:
        return None, None, 0
    
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol=SPY&token={FINNHUB_API_KEY}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data.get('c', 0)
            prev_close = data.get('pc', 0)
            
            if prev_close > 0:
                change_pct = (current - prev_close) / prev_close
            else:
                change_pct = 0
            
            spy_state["current_price"] = current
            spy_state["daily_change_pct"] = change_pct
            spy_state["last_check"] = datetime.now(timezone.utc)
            
            if change_pct <= SPY_DROP_THRESHOLD:
                spy_state["trend"] = "DOWN"
                spy_state["risk_off"] = True
                spy_state["longs_blocked_by_spy"] = SPY_BLOCK_LONGS_ON_DROP
            elif change_pct >= SPY_RALLY_THRESHOLD:
                spy_state["trend"] = "UP"
                spy_state["risk_off"] = False
                spy_state["longs_blocked_by_spy"] = False
            else:
                spy_state["trend"] = "NEUTRAL"
                spy_state["risk_off"] = False
                spy_state["longs_blocked_by_spy"] = False
            
            return current, prev_close, change_pct
            
    except Exception as e:
        print(f"SPY data error: {e}")
        return None, None, 0

# ================================
# MACRO INDICATORS (VIX & DXY)
# ================================
def get_vix_data():
    """Get VIX (Volatility Index) data from Finnhub API using VIXY ETF."""
    global macro_state
    
    if not VIX_ENABLED or not FINNHUB_API_KEY:
        return None, 0
    
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol=VIXY&token={FINNHUB_API_KEY}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data.get('c', 0)
            
            macro_state["vix_value"] = current
            
            if current >= VIX_HIGH_THRESHOLD:
                macro_state["vix_level"] = "EXTREME"
                macro_state["vix_longs_blocked"] = VIX_BLOCK_LONGS_ON_HIGH
            elif current >= 25:
                macro_state["vix_level"] = "HIGH"
                macro_state["vix_longs_blocked"] = False
            elif current <= VIX_LOW_THRESHOLD:
                macro_state["vix_level"] = "LOW"
                macro_state["vix_longs_blocked"] = False
            else:
                macro_state["vix_level"] = "NORMAL"
                macro_state["vix_longs_blocked"] = False
            
            return current, 0
            
    except Exception as e:
        print(f"VIX data error: {e}")
        return None, 0

def get_dxy_data():
    """Get DXY (Dollar Index) proxy data using UUP ETF from Finnhub."""
    global macro_state
    
    if not DXY_ENABLED or not FINNHUB_API_KEY:
        return None, 0
    
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol=UUP&token={FINNHUB_API_KEY}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data.get('c', 0)
            prev_close = data.get('pc', 0)
            
            if prev_close > 0:
                change_pct = (current - prev_close) / prev_close
            else:
                change_pct = 0
            
            macro_state["dxy_price"] = current
            macro_state["dxy_change_pct"] = change_pct
            
            if change_pct >= DXY_STRENGTH_THRESHOLD:
                macro_state["dxy_trend"] = "STRONG"
            elif change_pct <= DXY_WEAKNESS_THRESHOLD:
                macro_state["dxy_trend"] = "WEAK"
            else:
                macro_state["dxy_trend"] = "NEUTRAL"
            
            return current, change_pct
            
    except Exception as e:
        print(f"DXY data error: {e}")
        return None, 0

def run_macro_checks():
    """Run all macro indicator checks."""
    macro_state["last_check"] = datetime.now(timezone.utc)
    get_vix_data()
    get_dxy_data()

# ================================
# FOMC BLACKOUT FILTER
# ================================
def is_fomc_blackout():
    """Check if we're in FOMC blackout period."""
    if not FOMC_BLACKOUT_ENABLED:
        return False, None
    
    now = datetime.now(timezone.utc)
    for fomc_date in FOMC_DATES:
        blackout_start = fomc_date - timedelta(hours=FOMC_BLACKOUT_HOURS_BEFORE)
        blackout_end = fomc_date + timedelta(hours=FOMC_BLACKOUT_HOURS_AFTER)
        if blackout_start <= now <= blackout_end:
            return True, fomc_date
    return False, None

def get_next_fomc():
    """Get the next upcoming FOMC date."""
    now = datetime.now(timezone.utc)
    for fomc_date in FOMC_DATES:
        if fomc_date > now:
            return fomc_date
    return None

# ================================
# CIRCUIT BREAKER
# ================================
def check_circuit_breaker():
    """Check if circuit breaker should pause trading."""
    if not CIRCUIT_BREAKER_ENABLED:
        return False, None
    
    if circuit_state["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
        circuit_state["is_paused"] = True
        circuit_state["pause_reason"] = f"{MAX_CONSECUTIVE_LOSSES} consecutive losses"
        return True, circuit_state["pause_reason"]
    
    circuit_state["is_paused"] = False
    circuit_state["pause_reason"] = None
    return False, None

def record_signal_result(is_win):
    """Record signal result for circuit breaker."""
    global circuit_state
    
    if is_win:
        circuit_state["consecutive_losses"] = 0
        circuit_state["last_signal_result"] = "WIN"
    else:
        circuit_state["consecutive_losses"] += 1
        circuit_state["last_signal_result"] = "LOSS"
        
        # Log risk event for consecutive losses (v8.9.18)
        if circuit_state["consecutive_losses"] >= 2:
            severity = "warning" if circuit_state["consecutive_losses"] < MAX_CONSECUTIVE_LOSSES else "critical"
            log_risk_event("CONSECUTIVE_LOSSES", 
                          f"{circuit_state['consecutive_losses']} consecutive losses", 
                          severity)

def reset_circuit_breaker():
    """Reset circuit breaker after pause."""
    global circuit_state
    circuit_state["consecutive_losses"] = 0
    circuit_state["is_paused"] = False
    circuit_state["pause_reason"] = None

# ================================
# SIGNAL FILTERS CHECK
# ================================
def check_all_filters(direction, signal_data=None):
    """
    Check all filters before generating a signal.
    Returns: (is_blocked, block_reasons)
    
    Allows contrarian LONG when:
    - RSI < 30 (extreme oversold) even in bear market
    - Quant bias >= 15 (strong mathematical confirmation) - v8.9.2
    """
    blocked = False
    reasons = []
    
    # FOMC Blackout
    is_blackout, fomc_date = is_fomc_blackout()
    if is_blackout:
        blocked = True
        reasons.append(f"FOMC_BLACKOUT")
    
    # Circuit Breaker
    is_paused, pause_reason = check_circuit_breaker()
    if is_paused:
        blocked = True
        reasons.append(f"CIRCUIT_BREAKER")
    
    # For LONG signals, check additional filters
    if direction == "LONG":
        # Check for contrarian exception (extreme oversold = allowed to buy)
        rsi_val = signal_data.get('rsi', 50) if signal_data else 50
        is_extreme_oversold = rsi_val <= RSI_OVERSOLD  # Use constant (30.50)
        
        # v8.9.2: Check for strong quant confirmation (counter-trend exception)
        quant_bias = signal_data.get('quant_bias', 0) if signal_data else 0
        is_strong_quant = QUANT_COUNTER_TREND_ENABLED and quant_bias >= QUANT_COUNTER_TREND_MIN_BIAS
        
        # v8.9.19: Check for strong confluence bypass
        # v8.9.20: Restored strict threshold
        confluence_score = signal_data.get('confluence_score', 0) if signal_data else 0
        is_strong_confluence = confluence_score >= 55
        
        if is_extreme_oversold:
            # Allow contrarian LONG - skip bear market blocking
            reasons.append("CONTRARIAN_OVERSOLD")
        elif is_strong_quant:
            # v8.9.2: Allow counter-trend LONG with strong quant confirmation
            reasons.append(f"QUANT_COUNTER_TREND (+{quant_bias})")
        elif is_strong_confluence:
            # v8.9.19: Allow LONG with strong multi-indicator confluence
            reasons.append(f"CONFLUENCE_BYPASS ({confluence_score})")
        else:
            # Market Regime
            if market_regime_state.get("longs_blocked", False):
                regime = market_regime_state.get("regime", "NEUTRAL")
                if regime == "RANGE":
                    # In RANGE, only allow range scalps
                    blocked = True
                    reasons.append("RANGE_MARKET_NO_LONGS")
                else:
                    blocked = True
                    reasons.append("BEAR_MARKET")
            
            # SPY Risk-Off
            if spy_state.get("longs_blocked_by_spy", False):
                blocked = True
                reasons.append("SPY_RISK_OFF")
            
            # VIX Extreme
            if macro_state.get("vix_longs_blocked", False):
                blocked = True
                reasons.append("VIX_EXTREME")
    
    # v8.9.23: For SHORT signals, check market regime blocking
    if direction == "SHORT":
        # Check if SHORTs are blocked (BULL or RANGE market)
        if market_regime_state.get("shorts_blocked", False):
            regime = market_regime_state.get("regime", "NEUTRAL")
            if regime == "RANGE":
                # In RANGE, only allow range scalps (near S/R edges)
                if not market_regime_state.get("allow_range_scalps", False):
                    blocked = True
                    reasons.append("RANGE_MARKET_NO_SHORTS")
            else:
                blocked = True
                reasons.append("BULL_MARKET_NO_SHORTS")
    
    # v8.3: Fee Filter - check if TP1 profit covers trading fees
    if FEE_FILTER_ENABLED and signal_data:
        entry = signal_data.get('price', 0)
        tp1 = signal_data.get('tp1', 0)
        if entry > 0 and tp1 > 0:
            if signal_data.get('direction') == "LONG":
                tp1_profit_pct = ((tp1 - entry) / entry) * 100
            else:
                tp1_profit_pct = ((entry - tp1) / entry) * 100
            
            # Check if TP1 profit is less than minimum required
            if tp1_profit_pct < MIN_PROFIT_PCT:
                blocked = True
                reasons.append(f"FEE_FILTER (TP1={tp1_profit_pct:.2f}%<{MIN_PROFIT_PCT}%)")
    
    # v8.9.24: RR ENGINE v2.0 HYBRID - Dynamic min R:R + Soft penalty system
    # Factors: Market regime, trend strength, HTF bias, setup type, volatility
    if RR_FILTER_ENABLED and signal_data:
        entry = signal_data.get('price', 0)
        sl = signal_data.get('sl', 0)
        tp1 = signal_data.get('tp1', 0)
        direction = signal_data.get('direction', '')
        
        if entry > 0 and sl > 0 and tp1 > 0:
            if direction == "LONG":
                risk = entry - sl
                reward = tp1 - entry
            else:
                risk = sl - entry
                reward = entry - tp1
            
            if risk > 0:
                rr_ratio = reward / risk
                signal_data['rr_ratio'] = rr_ratio
                
                # Determine trend strength from signal data
                trend = signal_data.get('trend', 'NEUTRAL')
                if trend in ["STRONG_BULL", "STRONG_BEAR"]:
                    trend_strength = "STRONG"
                elif trend in ["BULL", "BEAR"]:
                    trend_strength = "NORMAL"
                else:
                    trend_strength = "WEAK"
                
                # Get ATR ratio (current ATR vs average)
                atr_ratio = signal_data.get('atr_ratio', 1.0)
                is_countertrend = signal_data.get('is_countertrend', False)
                current_score = signal_data.get('score', 50)
                
                # v8.9.24: Get additional context for Dynamic RR Engine
                market_regime = signal_data.get('market_regime', 'BULL')
                higher_tf_bias = signal_data.get('higher_tf_bias', 'NEUTRAL')
                setup_type = signal_data.get('setup_type', 'CONTINUATION')
                
                # Determine volatility level from ATR ratio
                if atr_ratio >= 1.4:
                    volatility_level = "HIGH"
                elif atr_ratio < 0.7:
                    volatility_level = "LOW"
                else:
                    volatility_level = "NORMAL"
                
                # v8.9.23: Check SCALP_REBOUND for min_rr override
                scalp_rebound_active = signal_data.get('scalp_rebound', False)
                scalp_rebound_min_rr = 0.5  # SCALP_REBOUND allows 0.5:1 min R:R
                
                # Evaluate using RR Engine v2.0 HYBRID
                rr_context = RRContext(
                    rr=rr_ratio,
                    score=current_score,
                    trend_strength=trend_strength,
                    atr_ratio=atr_ratio,
                    is_countertrend=is_countertrend,
                    market_regime=market_regime,
                    higher_tf_bias=higher_tf_bias,
                    setup_type=setup_type,
                    volatility_level=volatility_level
                )
                
                # v8.9.23: If SCALP_REBOUND active, override min_rr
                if scalp_rebound_active and rr_ratio >= scalp_rebound_min_rr:
                    # SCALP_REBOUND bypasses normal RR requirements
                    rr_result = RRResult(
                        allowed=True,
                        final_score=current_score,
                        min_rr=scalp_rebound_min_rr,
                        base_rr=1.2,
                        penalty=0,
                        scalp_mode=True,
                        reason="SCALP_REBOUND_OVERRIDE",
                        rr_adjustments="SCALP_REBOUND"
                    )
                else:
                    rr_result = evaluate_rr(rr_context)
                
                # Store results for later use
                signal_data['rr_result'] = rr_result
                signal_data['rr_penalty'] = rr_result.penalty
                signal_data['rr_min'] = rr_result.min_rr
                signal_data['rr_adjustments'] = rr_result.rr_adjustments
                signal_data['scalp_mode'] = rr_result.scalp_mode
                signal_data['trade_mode'] = "SCALP_REBOUND" if scalp_rebound_active else "NORMAL"
                
                if not rr_result.allowed:
                    blocked = True
                    reasons.append(f"RR_ENGINE ({rr_result.reason})")
    
    return blocked, reasons

# ================================
# CONSOLIDATION GUARD (v8.9.20)
# Professional traders don't trade in the middle of ranges
# ================================
CONSOLIDATION_GUARD_ENABLED = True
CONSOLIDATION_ATR_THRESHOLD = 0.6      # Minimum ATR% for volatility (below = sleeping)
CONSOLIDATION_RANGE_EDGE_PCT = 30      # Must be within 30% of range edge
CONSOLIDATION_MOMENTUM_MIN = 35        # Minimum wave/momentum score
CONSOLIDATION_ADX_MIN = 20             # Minimum ADX for trend strength

def is_consolidating(df, current_price, signal_data=None, lookback=50):
    """
    v8.9.20: ConsolidationGuard - Detect if price is in consolidation/range
    
    Professional traders avoid:
    1. Trading in the MIDDLE of a range (wait for breakout or entry near edges)
    2. Low volatility markets (price "sleeping")
    3. Weak momentum (no directional impulse)
    
    Returns: (is_consolidating, reasons)
    """
    if not CONSOLIDATION_GUARD_ENABLED or df is None or len(df) < lookback:
        return False, []
    
    # Defensive check for required columns
    required_cols = ['high', 'low', 'close']
    if not all(col in df.columns for col in required_cols):
        return False, ["MISSING_COLUMNS"]
    
    reasons = []
    consolidation_score = 0
    
    try:
        # 1. Calculate range (high-low over lookback period)
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        range_size = recent_high - recent_low
        range_mid = (recent_high + recent_low) / 2
        
        if range_size <= 0:
            return False, []
        
        # 2. Check position within range
        distance_from_high = abs(recent_high - current_price)
        distance_from_low = abs(current_price - recent_low)
        min_distance = min(distance_from_high, distance_from_low)
        distance_pct = (min_distance / range_size) * 100
        
        # If price is in middle of range (>30% from both edges)
        if distance_pct > CONSOLIDATION_RANGE_EDGE_PCT:
            consolidation_score += 30
            reasons.append(f"MID_RANGE ({100-distance_pct:.0f}% from edge)")
        
        # 3. Check ATR volatility
        atr = calc_atr(df, period=14)
        if atr is not None and len(atr) > 0:
            atr_value = atr.iloc[-1]
            atr_pct = (atr_value / current_price) * 100
            if atr_pct < CONSOLIDATION_ATR_THRESHOLD:
                consolidation_score += 25
                reasons.append(f"LOW_VOL (ATR={atr_pct:.2f}%<{CONSOLIDATION_ATR_THRESHOLD}%)")
        
        # 4. Check ADX (trend strength)
        adx, _, _ = calc_adx(df, period=14)
        if adx is not None and len(adx) > 0:
            adx_value = adx.iloc[-1]
            if adx_value < CONSOLIDATION_ADX_MIN:
                consolidation_score += 25
                reasons.append(f"WEAK_TREND (ADX={adx_value:.0f}<{CONSOLIDATION_ADX_MIN})")
        
        # 5. Check Bollinger Band squeeze (volatility contraction)
        bb_lower, bb_mid, bb_upper = calc_bollinger(df['close'], period=20)
        if bb_lower is not None and bb_upper is not None:
            bb_width = ((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_mid.iloc[-1]) * 100
            bb_width_median = ((bb_upper - bb_lower) / bb_mid * 100).tail(50).median()
            if bb_width < bb_width_median * 0.8:  # Below 80% of median = squeeze
                consolidation_score += 20
                reasons.append(f"BB_SQUEEZE ({bb_width:.1f}%)")
        
        # 6. Check signal momentum/wave score if available
        if signal_data:
            wave_score = signal_data.get('wave_score', 50)
            if wave_score < CONSOLIDATION_MOMENTUM_MIN:
                consolidation_score += 20
                reasons.append(f"WEAK_MOMENTUM (Wave={wave_score}<{CONSOLIDATION_MOMENTUM_MIN})")
        
        # Consolidation detected if score >= 50
        is_consol = consolidation_score >= 50
        
        return is_consol, reasons
        
    except Exception as e:
        return False, [f"ERROR: {str(e)}"]

# ================================
# INDICATOR FUNCTIONS
# ================================
def calc_rsi(close, period=14):
    return RSIIndicator(close, window=period).rsi()

def calc_ema(close, period):
    return EMAIndicator(close, window=period).ema_indicator()

def calc_adx(df, period=14):
    adx = ADXIndicator(df['high'], df['low'], df['close'], window=period)
    return adx.adx(), adx.adx_pos(), adx.adx_neg()

def calc_macd(close):
    macd = MACD(close)
    return macd.macd(), macd.macd_signal(), macd.macd_diff()

def calc_stochastic(df, period=14):
    stoch = StochasticOscillator(df['high'], df['low'], df['close'], window=period)
    return stoch.stoch(), stoch.stoch_signal()

def calc_bollinger(close, period=20):
    bb = BollingerBands(close, window=period)
    return bb.bollinger_lband(), bb.bollinger_mavg(), bb.bollinger_hband()

def calc_atr(df, period=14):
    return AverageTrueRange(df['high'], df['low'], df['close'], window=period).average_true_range()

# ================================
# VWAP CALCULATION (v8.9.9)
# ================================
def calc_vwap(df, period=None):
    """
    Calculate Volume Weighted Average Price (VWAP).
    
    VWAP = Σ(Typical Price × Volume) / Σ(Volume)
    where Typical Price = (High + Low + Close) / 3
    
    Args:
        df: DataFrame with OHLCV data
        period: If specified, calculate rolling VWAP for last N bars
                If None, calculate cumulative VWAP from start
    
    Returns:
        Series with VWAP values
    """
    if df is None or len(df) < 2:
        return None
    
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tp_volume = typical_price * df['volume']
    
    if period is None:
        cumulative_tp_vol = tp_volume.cumsum()
        cumulative_vol = df['volume'].cumsum()
        vwap = cumulative_tp_vol / cumulative_vol
    else:
        cumulative_tp_vol = tp_volume.rolling(window=period).sum()
        cumulative_vol = df['volume'].rolling(window=period).sum()
        vwap = cumulative_tp_vol / cumulative_vol
    
    return vwap

def get_vwap_signal(df, period=50):
    """
    Analyze price position relative to VWAP for signal filtering.
    
    Returns:
        dict with:
        - vwap: Current VWAP value
        - price_vs_vwap: 'ABOVE', 'BELOW', or 'AT_VWAP'
        - vwap_trend: 'RISING', 'FALLING', or 'FLAT'
        - bias: 'BULLISH', 'BEARISH', or 'NEUTRAL'
        - signal_boost: Points to add to signal score (-5 to +5)
    """
    if df is None or len(df) < period:
        return {
            'vwap': 0,
            'price_vs_vwap': 'UNKNOWN',
            'vwap_trend': 'UNKNOWN',
            'bias': 'NEUTRAL',
            'signal_boost': 0,
            'valid': False
        }
    
    vwap = calc_vwap(df, period)
    if vwap is None or len(vwap) < 5:
        return {
            'vwap': 0,
            'price_vs_vwap': 'UNKNOWN',
            'vwap_trend': 'UNKNOWN',
            'bias': 'NEUTRAL',
            'signal_boost': 0,
            'valid': False
        }
    
    current_vwap = vwap.iloc[-1]
    current_price = df['close'].iloc[-1]
    
    vwap_5_ago = vwap.iloc[-5] if len(vwap) >= 5 else vwap.iloc[0]
    vwap_change_pct = ((current_vwap - vwap_5_ago) / vwap_5_ago) * 100 if vwap_5_ago > 0 else 0
    
    distance_pct = ((current_price - current_vwap) / current_vwap) * 100 if current_vwap > 0 else 0
    
    if distance_pct > 0.3:
        price_vs_vwap = 'ABOVE'
    elif distance_pct < -0.3:
        price_vs_vwap = 'BELOW'
    else:
        price_vs_vwap = 'AT_VWAP'
    
    if vwap_change_pct > 0.1:
        vwap_trend = 'RISING'
    elif vwap_change_pct < -0.1:
        vwap_trend = 'FALLING'
    else:
        vwap_trend = 'FLAT'
    
    if price_vs_vwap == 'ABOVE' and vwap_trend == 'RISING':
        bias = 'BULLISH'
        signal_boost = 5
    elif price_vs_vwap == 'BELOW' and vwap_trend == 'FALLING':
        bias = 'BEARISH'
        signal_boost = -5
    elif price_vs_vwap == 'ABOVE':
        bias = 'BULLISH'
        signal_boost = 3
    elif price_vs_vwap == 'BELOW':
        bias = 'BEARISH'
        signal_boost = -3
    else:
        bias = 'NEUTRAL'
        signal_boost = 0
    
    return {
        'vwap': current_vwap,
        'price_vs_vwap': price_vs_vwap,
        'vwap_trend': vwap_trend,
        'bias': bias,
        'signal_boost': signal_boost,
        'distance_pct': distance_pct,
        'valid': True
    }

def check_vwap_filter(direction, vwap_signal):
    """
    Check if signal direction aligns with VWAP bias.
    
    Professional rule:
    - LONG only if price is ABOVE VWAP (pirkėjų kontrolė)
    - SHORT only if price is BELOW VWAP (pardavėjų kontrolė)
    
    Returns:
        (passed, reason) tuple
    """
    if not vwap_signal.get('valid', False):
        return True, "VWAP_NO_DATA"
    
    price_vs_vwap = vwap_signal.get('price_vs_vwap', 'UNKNOWN')
    bias = vwap_signal.get('bias', 'NEUTRAL')
    
    if direction == "LONG":
        if price_vs_vwap == 'BELOW' and bias == 'BEARISH':
            return False, f"VWAP_BLOCK (LONG žemiau VWAP, bias={bias})"
        return True, "VWAP_OK"
    
    elif direction == "SHORT":
        if price_vs_vwap == 'ABOVE' and bias == 'BULLISH':
            return False, f"VWAP_BLOCK (SHORT virš VWAP, bias={bias})"
        return True, "VWAP_OK"
    
    return True, "VWAP_OK"

# ================================
# SUPPORT/RESISTANCE ZONES (v8.7)
# ================================
def find_sr_zones(df, lookback=50, tolerance_pct=0.5):
    """
    Rasti support/resistance zonas pagal swing high/low taškus.
    
    Args:
        df: DataFrame su OHLCV duomenimis
        lookback: Kiek žvakių analizuoti
        tolerance_pct: Zonų grupavimo tolerancija (%)
    
    Returns:
        dict su 'supports' ir 'resistances' sąrašais (kiekvienas - lista kainų)
    """
    if df is None or len(df) < 20:
        return {"supports": [], "resistances": []}
    
    highs = df['high'].values[-lookback:]
    lows = df['low'].values[-lookback:]
    closes = df['close'].values[-lookback:]
    
    # Rasti swing high (local maxima) - 5 žvakių languose
    swing_highs = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
    
    # Rasti swing low (local minima) - 5 žvakių languose
    swing_lows = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])
    
    # Grupuoti panašias zonas (per tolerance_pct)
    def group_levels(levels, tolerance_pct):
        if not levels:
            return []
        levels = sorted(levels)
        grouped = []
        current_group = [levels[0]]
        
        for level in levels[1:]:
            if abs(level - current_group[-1]) / current_group[-1] * 100 < tolerance_pct:
                current_group.append(level)
            else:
                grouped.append(sum(current_group) / len(current_group))
                current_group = [level]
        grouped.append(sum(current_group) / len(current_group))
        return grouped
    
    supports = group_levels(swing_lows, tolerance_pct)
    resistances = group_levels(swing_highs, tolerance_pct)
    
    return {"supports": supports, "resistances": resistances}

# ================================
# S/R FLIP DETECTION (v8.9.10)
# ================================
# Kai palaikymas pramuša žemyn - tampa pasipriešinimu (S→R)
# Kai pasipriešinimas pramuša aukštyn - tampa palaikymu (R→S)

def detect_sr_flip(df, lookback=100, tolerance_pct=0.5):
    """
    Detect Support/Resistance flip zones.
    
    When price breaks below a support level and then returns to it,
    that level now acts as resistance (S→R flip).
    
    When price breaks above a resistance level and then returns to it,
    that level now acts as support (R→S flip).
    
    Args:
        df: DataFrame with OHLCV data
        lookback: How many bars to analyze for finding flip zones
        tolerance_pct: Tolerance for matching price to zones (%)
    
    Returns:
        dict: {
            'flip_zones': [(price, 'S_TO_R' | 'R_TO_S', strength), ...],
            'nearest_flip': {
                'price': float,
                'type': 'S_TO_R' | 'R_TO_S',
                'distance_pct': float
            },
            'has_flip_nearby': bool
        }
    """
    result = {
        'flip_zones': [],
        'nearest_flip': None,
        'has_flip_nearby': False
    }
    
    if df is None or len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    highs = data['high'].values
    lows = data['low'].values
    closes = data['close'].values
    current_price = closes[-1]
    
    half_lookback = lookback // 2
    
    first_half_highs = highs[:half_lookback]
    first_half_lows = lows[:half_lookback]
    second_half_highs = highs[half_lookback:]
    second_half_lows = lows[half_lookback:]
    second_half_closes = closes[half_lookback:]
    
    old_resistances = []
    for i in range(2, len(first_half_highs) - 2):
        if (first_half_highs[i] > first_half_highs[i-1] and 
            first_half_highs[i] > first_half_highs[i-2] and 
            first_half_highs[i] > first_half_highs[i+1] and 
            first_half_highs[i] > first_half_highs[i+2]):
            old_resistances.append(first_half_highs[i])
    
    old_supports = []
    for i in range(2, len(first_half_lows) - 2):
        if (first_half_lows[i] < first_half_lows[i-1] and 
            first_half_lows[i] < first_half_lows[i-2] and 
            first_half_lows[i] < first_half_lows[i+1] and 
            first_half_lows[i] < first_half_lows[i+2]):
            old_supports.append(first_half_lows[i])
    
    flip_zones = []
    
    for res_level in old_resistances:
        tolerance = res_level * (tolerance_pct / 100)
        
        broke_above = any(c > res_level + tolerance for c in second_half_closes)
        
        if broke_above:
            touches_as_support = sum(1 for l in second_half_lows 
                                     if abs(l - res_level) < tolerance * 2)
            
            if touches_as_support >= 1:
                strength = min(touches_as_support, 3)
                flip_zones.append((res_level, 'R_TO_S', strength))
    
    for sup_level in old_supports:
        tolerance = sup_level * (tolerance_pct / 100)
        
        broke_below = any(c < sup_level - tolerance for c in second_half_closes)
        
        if broke_below:
            touches_as_resistance = sum(1 for h in second_half_highs 
                                        if abs(h - sup_level) < tolerance * 2)
            
            if touches_as_resistance >= 1:
                strength = min(touches_as_resistance, 3)
                flip_zones.append((sup_level, 'S_TO_R', strength))
    
    result['flip_zones'] = flip_zones
    
    if flip_zones:
        nearest = min(flip_zones, key=lambda x: abs(x[0] - current_price))
        distance_pct = abs(nearest[0] - current_price) / current_price * 100
        
        result['nearest_flip'] = {
            'price': nearest[0],
            'type': nearest[1],
            'strength': nearest[2],
            'distance_pct': distance_pct
        }
        
        result['has_flip_nearby'] = distance_pct < 2.0
    
    return result

def get_sr_flip_signal_boost(direction, sr_flip_result, current_price):
    """
    Get signal score adjustment based on S/R flip zones.
    
    Rules:
    - LONG near R→S flip (former resistance now support) = +10 boost
    - SHORT near S→R flip (former support now resistance) = +10 boost
    - Opposite direction near flip = -10 (risky entry)
    
    Returns:
        (boost, reason) tuple
    """
    if not sr_flip_result.get('has_flip_nearby', False):
        return 0, None
    
    nearest = sr_flip_result.get('nearest_flip')
    if not nearest:
        return 0, None
    
    flip_price = nearest['price']
    flip_type = nearest['type']
    distance_pct = nearest['distance_pct']
    
    if distance_pct > 1.5:
        return 0, None
    
    if direction == "LONG":
        if flip_type == 'R_TO_S' and current_price >= flip_price * 0.995:
            return 10, f"SR_FLIP_BOOST (R→S palaikymas ties ${flip_price:.2f})"
        elif flip_type == 'S_TO_R' and current_price <= flip_price * 1.005:
            return -10, f"SR_FLIP_RISK (S→R pasipriešinimas virš ${flip_price:.2f})"
    
    elif direction == "SHORT":
        if flip_type == 'S_TO_R' and current_price <= flip_price * 1.005:
            return 10, f"SR_FLIP_BOOST (S→R pasipriešinimas ties ${flip_price:.2f})"
        elif flip_type == 'R_TO_S' and current_price >= flip_price * 0.995:
            return -10, f"SR_FLIP_RISK (R→S palaikymas po ${flip_price:.2f})"
    
    return 0, None

# ================================
# MARKET STRUCTURE ANALYSIS (v8.9.8)
# ================================
# Based on trading education: HH/HL = Uptrend, LH/LL = Downtrend
# Structure break detection for trend change confirmation

def find_swing_points(df, lookback=30, swing_strength=2):
    """
    Rasti swing high ir swing low taškus su jų indeksais.
    
    Args:
        df: DataFrame su OHLCV duomenimis
        lookback: Kiek žvakių analizuoti
        swing_strength: Kiek žvakių iš abiejų pusių turi būti žemesnės/aukštesnės
    
    Returns:
        dict su 'swing_highs' ir 'swing_lows' (kiekvienas - lista tuple (index, price))
    """
    if df is None or len(df) < lookback:
        return {"swing_highs": [], "swing_lows": []}
    
    data = df.tail(lookback).reset_index(drop=True)
    highs = data['high'].values
    lows = data['low'].values
    
    swing_highs = []
    swing_lows = []
    
    for i in range(swing_strength, len(highs) - swing_strength):
        # Check swing high
        is_swing_high = True
        for j in range(1, swing_strength + 1):
            if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                is_swing_high = False
                break
        if is_swing_high:
            swing_highs.append((i, highs[i]))
        
        # Check swing low
        is_swing_low = True
        for j in range(1, swing_strength + 1):
            if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                is_swing_low = False
                break
        if is_swing_low:
            swing_lows.append((i, lows[i]))
    
    return {"swing_highs": swing_highs, "swing_lows": swing_lows}

def analyze_market_structure(df, lookback=50):
    """
    Analizuoti rinkos struktūrą ir nustatyti HH/HL/LH/LL sekas.
    
    Uptrend: HH (Higher High) + HL (Higher Low)
    Downtrend: LH (Lower High) + LL (Lower Low)
    
    Returns:
        dict: {
            'structure': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'swing_highs': [(idx, price, 'HH'|'LH'), ...],
            'swing_lows': [(idx, price, 'HL'|'LL'), ...],
            'last_swing_high': (idx, price, type),
            'last_swing_low': (idx, price, type),
            'structure_break': None | 'BULLISH_BREAK' | 'BEARISH_BREAK',
            'hh_count': int,
            'hl_count': int,
            'lh_count': int,
            'll_count': int
        }
    """
    result = {
        'structure': 'NEUTRAL',
        'swing_highs': [],
        'swing_lows': [],
        'last_swing_high': None,
        'last_swing_low': None,
        'structure_break': None,
        'hh_count': 0,
        'hl_count': 0,
        'lh_count': 0,
        'll_count': 0
    }
    
    if df is None or len(df) < 20:
        return result
    
    swings = find_swing_points(df, lookback=lookback, swing_strength=2)
    
    # Classify swing highs (HH or LH)
    prev_high = None
    for idx, price in swings['swing_highs']:
        if prev_high is None:
            swing_type = 'HH'  # First one is neutral
        elif price > prev_high:
            swing_type = 'HH'
            result['hh_count'] += 1
        else:
            swing_type = 'LH'
            result['lh_count'] += 1
        
        result['swing_highs'].append((idx, price, swing_type))
        prev_high = price
    
    # Classify swing lows (HL or LL)
    prev_low = None
    for idx, price in swings['swing_lows']:
        if prev_low is None:
            swing_type = 'HL'  # First one is neutral
        elif price > prev_low:
            swing_type = 'HL'
            result['hl_count'] += 1
        else:
            swing_type = 'LL'
            result['ll_count'] += 1
        
        result['swing_lows'].append((idx, price, swing_type))
        prev_low = price
    
    # Set last swing points
    if result['swing_highs']:
        result['last_swing_high'] = result['swing_highs'][-1]
    if result['swing_lows']:
        result['last_swing_low'] = result['swing_lows'][-1]
    
    # Determine overall structure
    # Look at last 3 swing points for recent structure
    recent_highs = result['swing_highs'][-3:] if len(result['swing_highs']) >= 2 else result['swing_highs']
    recent_lows = result['swing_lows'][-3:] if len(result['swing_lows']) >= 2 else result['swing_lows']
    
    recent_hh = sum(1 for _, _, t in recent_highs if t == 'HH')
    recent_lh = sum(1 for _, _, t in recent_highs if t == 'LH')
    recent_hl = sum(1 for _, _, t in recent_lows if t == 'HL')
    recent_ll = sum(1 for _, _, t in recent_lows if t == 'LL')
    
    # BULLISH: Mostly HH and HL
    if recent_hh >= recent_lh and recent_hl >= recent_ll:
        result['structure'] = 'BULLISH'
    # BEARISH: Mostly LH and LL
    elif recent_lh >= recent_hh and recent_ll >= recent_hl:
        result['structure'] = 'BEARISH'
    else:
        result['structure'] = 'NEUTRAL'
    
    # Detect structure break
    # BULLISH_BREAK: Was making LL, now made HL (potential uptrend start)
    # BEARISH_BREAK: Was making HH/HL, now made LH + LL (potential downtrend start)
    if len(result['swing_lows']) >= 2:
        last_two_lows = result['swing_lows'][-2:]
        if last_two_lows[0][2] == 'LL' and last_two_lows[1][2] == 'HL':
            result['structure_break'] = 'BULLISH_BREAK'
    
    if len(result['swing_highs']) >= 2:
        last_two_highs = result['swing_highs'][-2:]
        if last_two_highs[0][2] == 'HH' and last_two_highs[1][2] == 'LH':
            result['structure_break'] = 'BEARISH_BREAK'
    
    # v8.9.12: CHoCH (Change of Character) Detection
    # CHoCH = First sign of potential trend reversal (different from BOS)
    # BOS confirms continuation, CHoCH signals potential reversal
    # CHoCH is the FIRST HH after a sequence of LH (or first LL after sequence of HL)
    result['choch'] = None
    result['choch_level'] = None
    
    # Bullish CHoCH: In downtrend, price makes first HH after ANY sequence of LHs
    # Pattern: ...LH -> HH = CHoCH Bullish (sellers losing control)
    if len(result['swing_highs']) >= 2:
        # Find if the last swing high is HH and previous was LH
        last_high = result['swing_highs'][-1]
        prev_high = result['swing_highs'][-2]
        
        if last_high[2] == 'HH' and prev_high[2] == 'LH':
            result['choch'] = 'BULLISH_CHOCH'
            result['choch_level'] = prev_high[1]  # Previous LH level (breakout point)
    
    # Bearish CHoCH: In uptrend, price makes first LL after ANY sequence of HLs
    # Pattern: ...HL -> LL = CHoCH Bearish (buyers losing control)
    if len(result['swing_lows']) >= 2 and result['choch'] is None:
        last_low = result['swing_lows'][-1]
        prev_low = result['swing_lows'][-2]
        
        if last_low[2] == 'LL' and prev_low[2] == 'HL':
            result['choch'] = 'BEARISH_CHOCH'
            result['choch_level'] = prev_low[1]  # Previous HL level (breakdown point)
    
    return result

def get_structure_signal_filter(structure_result, direction):
    """
    Filtruoti signalus pagal market structure.
    
    Args:
        structure_result: Rezultatas iš analyze_market_structure()
        direction: 'LONG' arba 'SHORT'
    
    Returns:
        (allowed: bool, reason: str)
    """
    structure = structure_result.get('structure', 'NEUTRAL')
    structure_break = structure_result.get('structure_break')
    choch = structure_result.get('choch')
    
    if direction == "LONG":
        # LONG leidžiamas:
        # 1. BULLISH structure (HH+HL seka)
        # 2. BULLISH_BREAK (struktūra keičiasi į bullish)
        # 3. BULLISH_CHOCH (pirmas apsisukimo ženklas)
        # 4. NEUTRAL (neaiški kryptis)
        if structure == 'BULLISH':
            return True, f"STRUCTURE_OK (BULLISH: HH={structure_result['hh_count']} HL={structure_result['hl_count']})"
        elif choch == 'BULLISH_CHOCH':
            return True, "CHoCH_BULLISH (reversal signal)"
        elif structure_break == 'BULLISH_BREAK':
            return True, "STRUCTURE_BREAK_BULLISH"
        elif structure == 'NEUTRAL':
            return True, "STRUCTURE_NEUTRAL"
        else:
            # BEARISH structure - leidžiame, bet su įspėjimu
            return True, f"STRUCTURE_WARNING (BEARISH: LH={structure_result['lh_count']} LL={structure_result['ll_count']})"
    
    else:  # SHORT
        # SHORT leidžiamas:
        # 1. BEARISH structure (LH+LL seka)
        # 2. BEARISH_BREAK (struktūra keičiasi į bearish)
        # 3. BEARISH_CHOCH (pirmas apsisukimo ženklas)
        # 4. NEUTRAL
        if structure == 'BEARISH':
            return True, f"STRUCTURE_OK (BEARISH: LH={structure_result['lh_count']} LL={structure_result['ll_count']})"
        elif choch == 'BEARISH_CHOCH':
            return True, "CHoCH_BEARISH (reversal signal)"
        elif structure_break == 'BEARISH_BREAK':
            return True, "STRUCTURE_BREAK_BEARISH"
        elif structure == 'NEUTRAL':
            return True, "STRUCTURE_NEUTRAL"
        else:
            # BULLISH structure - leidžiame, bet su įspėjimu
            return True, f"STRUCTURE_WARNING (BULLISH: HH={structure_result['hh_count']} HL={structure_result['hl_count']})"

def get_tp1_from_sr_zone(current_price, direction, df, atr_val, min_distance_atr=1.0, max_distance_atr=4.0):
    """
    Nustatyti TP1 pagal artimiausią S/R zoną.
    
    Args:
        current_price: Dabartinė kaina
        direction: "LONG" arba "SHORT"
        df: DataFrame su OHLCV duomenimis
        atr_val: ATR reikšmė (naudojama min/max atstumui)
        min_distance_atr: Minimalus atstumas ATR vienetais
        max_distance_atr: Maksimalus atstumas ATR vienetais
    
    Returns:
        tp1: TP1 kaina, zone_used: ar naudota S/R zona
    """
    # Default ATR-based TP1 (guaranteed fallback)
    atr_tp1_long = current_price + (atr_val * 2.5)
    atr_tp1_short = current_price - (atr_val * 2.5)
    
    zones = find_sr_zones(df, lookback=50)
    
    min_distance = atr_val * min_distance_atr
    max_distance = atr_val * max_distance_atr
    
    if direction == "LONG":
        # Ieškoti artimiausio resistance VIRŠ kainos
        valid_resistances = [r for r in zones['resistances'] 
                           if r > current_price + min_distance and r < current_price + max_distance]
        if valid_resistances:
            tp1 = min(valid_resistances)  # Artimiausias resistance
            # Validacija: TP1 PRIVALO būti aukščiau entry
            if tp1 > current_price:
                return tp1, True
        # Fallback: ATR-based TP1
        return atr_tp1_long, False
    else:  # SHORT
        # Ieškoti artimiausio support ŽEMIAU kainos
        valid_supports = [s for s in zones['supports'] 
                         if s < current_price - min_distance and s > current_price - max_distance]
        if valid_supports:
            tp1 = max(valid_supports)  # Artimiausias support
            # Validacija: TP1 PRIVALO būti žemiau entry
            if tp1 < current_price:
                return tp1, True
        # Fallback: ATR-based TP1
        return atr_tp1_short, False

# ================================
# DATA FETCHING
# ================================
async def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe}: {e}")
        return None

# ================================
# MACRO ANALYSIS (4H) - Big Picture
# ================================
def analyze_macro(df):
    """Analyze 4H timeframe for macro context."""
    if df is None or len(df) < 50:
        return {"bias": "NEUTRAL", "score": 0, "signals": []}
    
    close = df['close']
    signals = []
    macro_score = 0
    
    # EMA alignment
    ema20 = calc_ema(close, 20).iloc[-1]
    ema50 = calc_ema(close, 50).iloc[-1]
    current_price = close.iloc[-1]
    
    if current_price > ema20 > ema50:
        macro_score += 25
        signals.append("4H_EMA_BULL")
    elif current_price < ema20 < ema50:
        macro_score -= 25
        signals.append("4H_EMA_BEAR")
    
    # ADX trend strength on 4H
    adx, plus_di, minus_di = calc_adx(df)
    adx_val = adx.iloc[-1]
    
    if adx_val > 25:
        if plus_di.iloc[-1] > minus_di.iloc[-1]:
            macro_score += 20
            signals.append("4H_STRONG_TREND_UP")
        else:
            macro_score -= 20
            signals.append("4H_STRONG_TREND_DOWN")
    
    # RSI for overbought/oversold on macro
    rsi = calc_rsi(close)
    rsi_val = rsi.iloc[-1]
    
    if rsi_val < 30:
        signals.append("4H_OVERSOLD")
        macro_score += 10  # Potential bounce
    elif rsi_val > 70:
        signals.append("4H_OVERBOUGHT")
        macro_score -= 10  # Potential drop
    
    # BB Squeeze on 4H - breakout potential
    is_squeeze, squeeze_strength = calculate_bb_squeeze(df)
    if is_squeeze:
        signals.append("4H_BB_SQUEEZE")
    
    # Determine macro bias
    if macro_score >= 30:
        bias = "STRONG_BULL"
    elif macro_score >= 15:
        bias = "BULL"
    elif macro_score <= -30:
        bias = "STRONG_BEAR"
    elif macro_score <= -15:
        bias = "BEAR"
    else:
        bias = "NEUTRAL"
    
    return {
        "bias": bias,
        "score": macro_score,
        "signals": signals,
        "rsi": rsi_val,
        "adx": adx_val,
        "squeeze": is_squeeze
    }

# ================================
# TREND ANALYSIS (1H)
# ================================
def analyze_trend(df):
    if df is None or len(df) < 50:
        return "NEUTRAL", 0
    
    close = df['close']
    
    ema20 = calc_ema(close, 20).iloc[-1]
    ema50 = calc_ema(close, 50).iloc[-1]
    current_price = close.iloc[-1]
    
    adx, plus_di, minus_di = calc_adx(df)
    adx_val = adx.iloc[-1]
    plus_di_val = plus_di.iloc[-1]
    minus_di_val = minus_di.iloc[-1]
    
    macd_line, signal_line, macd_hist = calc_macd(close)
    macd_bullish = macd_line.iloc[-1] > signal_line.iloc[-1]
    
    trend_score = 0
    trend = "NEUTRAL"
    
    if current_price > ema20 > ema50:
        trend_score += 30
    elif current_price < ema20 < ema50:
        trend_score -= 30
    
    if adx_val > ADX_STRONG:
        if plus_di_val > minus_di_val:
            trend_score += 25
        else:
            trend_score -= 25
    elif adx_val > ADX_MIN:
        if plus_di_val > minus_di_val:
            trend_score += 15
        else:
            trend_score -= 15
    
    if macd_bullish:
        trend_score += 20
    else:
        trend_score -= 20
    
    if trend_score >= 40:
        trend = "STRONG_BULL"
    elif trend_score >= 20:
        trend = "BULL"
    elif trend_score <= -40:
        trend = "STRONG_BEAR"
    elif trend_score <= -20:
        trend = "BEAR"
    else:
        trend = "NEUTRAL"
    
    return trend, trend_score

# ================================
# PULLBACK COMPLETION FILTER (v8.2)
# ================================
def pullback_complete_short(df, ema21_val):
    """
    Patikrina ar pullback pasibaigęs prieš SHORT įėjimą.
    Reikalauja:
    1. Paskutinė kaina < EMA21 (grįžo po pullback)
    2. Paskutinė kaina < ankstesnio swing low (struktūra palūžo)
    3. Stochastic %K kerta žemyn iš >70 zonos (momentum patvirtinimas)
    """
    if df is None or len(df) < 10:
        return False, "INSUFFICIENT_DATA"
    
    close = df['close']
    current_price = close.iloc[-1]
    
    # 1. Kaina turi būti žemiau EMA21 (pullback pasibaigęs)
    if current_price >= ema21_val:
        return False, "PRICE_ABOVE_EMA21"
    
    # 2. Kaina turi būti žemiau ankstesnio swing low (last 4-6 candles min)
    prior_swing_low = close.iloc[-6:-1].min()
    if current_price >= prior_swing_low:
        return False, "NO_SWING_BREAK"
    
    # 3. Stochastic turėtų būti krentantis iš overbought (optional extra confirmation)
    stoch = StochasticOscillator(df['high'], df['low'], close, window=14, smooth_window=3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_k_prev = stoch.stoch().iloc[-2] if len(stoch.stoch()) > 1 else stoch_k
    
    # Stochastic kerta žemyn ARBA jau žemai (< 50)
    stoch_ok = stoch_k < stoch_k_prev or stoch_k < 50
    
    if not stoch_ok:
        return False, "STOCH_NOT_CONFIRMED"
    
    return True, "PULLBACK_COMPLETE"

def pullback_complete_long(df, ema21_val):
    """
    Patikrina ar pullback pasibaigęs prieš LONG įėjimą.
    """
    if df is None or len(df) < 10:
        return False, "INSUFFICIENT_DATA"
    
    close = df['close']
    current_price = close.iloc[-1]
    
    # 1. Kaina turi būti aukščiau EMA21
    if current_price <= ema21_val:
        return False, "PRICE_BELOW_EMA21"
    
    # 2. Kaina turi būti aukščiau ankstesnio swing high
    prior_swing_high = close.iloc[-6:-1].max()
    if current_price <= prior_swing_high:
        return False, "NO_SWING_BREAK"
    
    # 3. Stochastic kyla arba jau aukštai
    stoch = StochasticOscillator(df['high'], df['low'], close, window=14, smooth_window=3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_k_prev = stoch.stoch().iloc[-2] if len(stoch.stoch()) > 1 else stoch_k
    
    stoch_ok = stoch_k > stoch_k_prev or stoch_k > 50
    
    if not stoch_ok:
        return False, "STOCH_NOT_CONFIRMED"
    
    return True, "PULLBACK_COMPLETE"

def get_volatility_adjusted_atr_multiplier(df_1h, vix_value):
    """
    Padidinti ATR multiplier kai volatility aukštas.
    Normalu: 2.0x ATR
    Aukštas VIX (>25): 2.5x ATR
    Labai aukštas VIX (>30): 3.0x ATR
    """
    base_multiplier = 2.0
    volatility_spike = False
    
    # VIX adjustment
    if vix_value and vix_value >= 30:
        base_multiplier = 3.0
        volatility_spike = True
    elif vix_value and vix_value >= 25:
        base_multiplier = 2.5
    
    # 1H ATR volatility check
    if df_1h is not None and len(df_1h) >= 20:
        atr_1h = AverageTrueRange(df_1h['high'], df_1h['low'], df_1h['close'], window=14).average_true_range()
        current_atr = atr_1h.iloc[-1]
        avg_atr = atr_1h.rolling(20).mean().iloc[-1]
        
        # Jei dabartinis ATR > 1.3x vidutinio = didelis volatility
        if avg_atr > 0 and current_atr > avg_atr * 1.3:
            base_multiplier = max(base_multiplier, 2.5)
        
        # Log volatility spike if ATR > 1.5x average (v8.9.18)
        if avg_atr > 0 and current_atr > avg_atr * 1.5:
            volatility_spike = True
            log_risk_event("VOLATILITY_SPIKE", 
                          f"ATR {current_atr:.2f} > 1.5x avg ({avg_atr:.2f})", 
                          "warning")
    
    return base_multiplier

def check_volume_confirmation(df, direction):
    """
    Patikrinti ar volume patvirtina įėjimą.
    SHORT: dabartinis volume >= 1.1x vidurkio + ankstesnės žvakės mažėjantis volume (exhaustion)
    LONG: dabartinis volume >= 1.1x vidurkio + ankstesnės žvakės mažėjantis volume
    """
    if df is None or len(df) < 25 or 'volume' not in df.columns:
        return True, "NO_VOLUME_DATA"  # Jei nėra volume duomenų, leidžiam
    
    volume = df['volume']
    current_vol = volume.iloc[-1]
    avg_vol = volume.rolling(20).mean().iloc[-1]
    
    if avg_vol == 0:
        return True, "NO_VOLUME_DATA"
    
    # Dabartinis volume turi būti >= 1.1x vidurkio
    vol_spike = current_vol >= avg_vol * 1.1
    
    # Ankstesnės 3 žvakės turėtų turėti mažėjantį volume (pullback exhaustion)
    prev_vols = volume.iloc[-4:-1]
    vol_declining = prev_vols.iloc[0] > prev_vols.iloc[-1]  # Bendra tendencija žemyn
    
    if vol_spike:
        return True, "VOLUME_SPIKE"
    elif vol_declining:
        return True, "VOLUME_EXHAUSTION"
    else:
        return False, "WEAK_VOLUME"

# ================================
# INTEGRATED SIGNAL SCORING
# ================================
def get_integrated_score(symbol: str, df_htf, df_ltf, base_direction: str, base_score: int) -> dict:
    """
    Integruotas signalo vertinimas naudojant visus modulius:
    - Pro Trader strategijos (CryptoCred, DonAlt, Hsaka, Scott Melker)
    - Kvantitatyvinė analizė (Monte Carlo, ARIMA, Mean Reversion)
    - Sentimento analizė (Reddit, Fear & Greed)
    - On-chain metrikos (Whale activity, Exchange flows)
    - ML modelis (jei ištreniruotas)
    """
    asset_name = ASSET_NAMES.get(symbol, symbol.replace('PF_', '').replace('USD', ''))
    adjustments = []
    total_adjustment = 0
    confidence_factors = []
    
    try:
        pro_signal = pro_analyzer.analyze(df_htf, df_ltf)
        if pro_signal.direction == base_direction:
            adjustment = min(25, pro_signal.score // 3)
            total_adjustment += adjustment
            # Filter strategies to only include those matching direction
            direction_keywords = {
                'LONG': ['BULLISH', 'BULL', 'BUY', 'ACCUMULATION', 'RANGE_LOW'],
                'SHORT': ['BEARISH', 'BEAR', 'SELL', 'DISTRIBUTION', 'RANGE_HIGH']
            }
            keywords = direction_keywords.get(base_direction, [])
            filtered_strats = [s for s in pro_signal.strategies if any(k in s for k in keywords)]
            adjustments.extend(filtered_strats[:3])
            confidence_factors.append(('PRO', pro_signal.confidence))
        elif pro_signal.direction != 'NEUTRAL' and pro_signal.direction != base_direction:
            total_adjustment -= 15
            adjustments.append(f"PRO_CONFLICT_{pro_signal.direction}")
    except Exception as e:
        print(f"Pro analysis error: {e}")
    
    quant_bias_value = 0  # v8.9.2: Track quant bias for counter-trend logic
    try:
        if asset_name in quant_results and quant_results[asset_name]:
            quant_bias, quant_signals = quant_engine.get_quant_signal_bias(quant_results[asset_name])
            quant_bias_value = quant_bias  # Store for return
            
            if base_direction == "LONG" and quant_bias > 0:
                adjustment = min(20, quant_bias)
                total_adjustment += adjustment
                adjustments.extend([s for s in quant_signals[:2]])
                confidence_factors.append(('QUANT', min(1.0, abs(quant_bias) / 30)))
            elif base_direction == "SHORT" and quant_bias < 0:
                adjustment = min(20, abs(quant_bias))
                total_adjustment += adjustment
                adjustments.extend([s for s in quant_signals[:2]])
                confidence_factors.append(('QUANT', min(1.0, abs(quant_bias) / 30)))
            elif quant_bias != 0:
                total_adjustment -= 10
                adjustments.append("QUANT_CONFLICT")
    except Exception as e:
        print(f"Quant integration error: {e}")
    
    try:
        sentiment_data = sentiment_analyzer.get_reddit_sentiment(asset_name)
        sentiment_score = sentiment_data.get('sentiment_score', 0)
        
        if base_direction == "LONG" and sentiment_score > 0.1:
            adjustment = min(10, int(sentiment_score * 30))
            total_adjustment += adjustment
            adjustments.append(f"SENTIMENT_{sentiment_data.get('sentiment_label', 'BULLISH')}")
            confidence_factors.append(('SENTIMENT', min(1.0, abs(sentiment_score) * 2)))
        elif base_direction == "SHORT" and sentiment_score < -0.1:
            adjustment = min(10, int(abs(sentiment_score) * 30))
            total_adjustment += adjustment
            adjustments.append(f"SENTIMENT_{sentiment_data.get('sentiment_label', 'BEARISH')}")
            confidence_factors.append(('SENTIMENT', min(1.0, abs(sentiment_score) * 2)))
        elif abs(sentiment_score) > 0.2:
            if (base_direction == "LONG" and sentiment_score < -0.2) or \
               (base_direction == "SHORT" and sentiment_score > 0.2):
                total_adjustment -= 5
                adjustments.append("SENTIMENT_CONFLICT")
    except Exception as e:
        print(f"Sentiment integration error: {e}")
    
    try:
        onchain_data = onchain_analytics.get_comprehensive_analysis(asset_name)
        onchain_signal = onchain_data.get('overall_signal', 'NEUTRAL')
        onchain_score = onchain_data.get('onchain_score', 0)
        
        if base_direction == "LONG" and onchain_signal == "BULLISH":
            adjustment = min(15, abs(onchain_score) // 2)
            total_adjustment += adjustment
            adjustments.append("WHALE_ACCUMULATION")
            confidence_factors.append(('ONCHAIN', min(1.0, abs(onchain_score) / 35)))
        elif base_direction == "SHORT" and onchain_signal == "BEARISH":
            adjustment = min(15, abs(onchain_score) // 2)
            total_adjustment += adjustment
            adjustments.append("WHALE_DISTRIBUTION")
            confidence_factors.append(('ONCHAIN', min(1.0, abs(onchain_score) / 35)))
        elif onchain_signal != 'NEUTRAL':
            total_adjustment -= 5
            adjustments.append("ONCHAIN_CONFLICT")
    except Exception as e:
        print(f"Onchain integration error: {e}")
    
    try:
        ml_stats = ml_predictor.get_model_stats()
        if ml_stats.get('is_trained', False):
            confidence_factors.append(('ML', ml_stats.get('accuracy', 0.5)))
    except Exception as e:
        pass
    
    avg_confidence = sum(c[1] for c in confidence_factors) / len(confidence_factors) if confidence_factors else 0.5
    
    final_score = base_score + total_adjustment
    
    return {
        'final_score': min(100, max(0, final_score)),
        'adjustment': total_adjustment,
        'signals': adjustments,
        'confidence': avg_confidence,
        'modules_used': len(confidence_factors),
        'quant_bias': quant_bias_value  # v8.9.2: For counter-trend logic
    }


# ================================
# ORDER FLOW FILTER (v8.9.22)
# ================================
def order_flow_filter(df, direction: str) -> tuple:
    """
    Order flow inspired filter:
    - Absorption detection (high volume, small range = accumulation/distribution)
    - Fake breakout filter (big range, low volume = trap)
    - Momentum confirmation (follow-through)
    
    Returns:
        (bool, str): (ar_praėjo_filtrą, paaiškinimas)
    """
    if df is None or len(df) < 22:
        return True, "OF_NO_DATA"
    
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # === BASIC METRICS ===
        range_candle = last['high'] - last['low']
        avg_range = (df['high'] - df['low']).rolling(20).mean().iloc[-1]
        
        volume = last['volume']
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]
        
        body = abs(last['close'] - last['open'])
        wick = range_candle - body
        
        # Avoid division by zero
        if avg_range == 0 or avg_volume == 0 or body == 0:
            return True, "OF_CALC_ERROR"
        
        # === A. ABSORPTION ===
        # High volume + small range = institucinis pirkimas/pardavimas
        absorption = (
            volume > avg_volume * 1.5 and
            range_candle < avg_range * 0.7
        )
        
        # === B. FAKE BREAKOUT ===
        # Big range + low volume = spąstai
        fake_breakout = (
            range_candle > avg_range * 1.2 and
            volume < avg_volume
        )
        
        # === C. FOLLOW-THROUGH ===
        # Kaina juda kryptingai
        follow_through = (
            abs(last['close'] - prev['close']) > avg_range * 0.3
        )
        
        # === DIRECTIONAL LOGIC ===
        if direction == "LONG":
            # Bullish rejection - close above open, long lower wick
            rejection = last['close'] > last['open'] and wick > body * 1.2
        else:
            # Bearish rejection - close below open, long upper wick
            rejection = last['close'] < last['open'] and wick > body * 1.2
        
        # === FINAL DECISION ===
        if fake_breakout:
            return False, "OF_FAKE_BREAKOUT"
        
        if absorption and rejection:
            return True, "OF_ABSORPTION_REJECTION"
        
        if follow_through:
            return True, "OF_FOLLOW_THROUGH"
        
        # Default: allow if no fake breakout detected
        return True, "OF_NEUTRAL"
        
    except Exception as e:
        return True, f"OF_ERROR"


# ================================
# 1H RSI PRE-FILTER (v8.8 - The Rumers inspired)
# ================================
def check_1h_rsi_prefilter(df_1h, direction: str, lookback: int = 6) -> tuple:
    """
    Patikrinti ar 1H RSI buvo oversold/overbought per paskutines N valandų.
    
    Logika (pagal The Rumers video):
    - LONG: 1H RSI buvo <= 32 per paskutines 6 valandas (oversold zona)
    - SHORT: 1H RSI buvo >= 68 per paskutines 6 valandas (overbought zona)
    
    Returns:
        (bool, str): (ar_praėjo_filtrą, paaiškinimas)
    """
    if df_1h is None or len(df_1h) < lookback + 14:
        return True, "1H_RSI_NO_DATA"
    
    rsi_1h = calc_rsi(df_1h['close'])
    if rsi_1h is None or len(rsi_1h) < lookback:
        return True, "1H_RSI_CALC_ERROR"
    
    rsi_window = rsi_1h.iloc[-lookback:]
    min_rsi = rsi_window.min()
    max_rsi = rsi_window.max()
    current_rsi = rsi_1h.iloc[-1]
    
    if direction == "LONG":
        was_oversold = min_rsi <= 32
        if was_oversold:
            return True, f"1H_RSI_OVERSOLD_{min_rsi:.0f}"
        else:
            return False, f"1H_RSI_NOT_OVERSOLD (min={min_rsi:.0f}, need<=32)"
    
    elif direction == "SHORT":
        was_overbought = max_rsi >= 68
        if was_overbought:
            return True, f"1H_RSI_OVERBOUGHT_{max_rsi:.0f}"
        else:
            return False, f"1H_RSI_NOT_OVERBOUGHT (max={max_rsi:.0f}, need>=68)"
    
    return True, "1H_RSI_NEUTRAL"


# ================================
# MULTI-INDICATOR CONFLUENCE BYPASS (v8.9.19)
# ================================
def check_multi_indicator_confluence(df, df_1h, df_htf, direction: str) -> tuple:
    """
    Patikrinti ar yra stipri multi-indikatorių konfluencija.
    Kai konfluencija yra labai stipri, galima apeiti RSI filtrą.
    
    Konfluencijos elementai:
    1. EMA Stack (9 > 21 > 50 for LONG, opposite for SHORT)
    2. Wave/Momentum Alignment (rising/falling across timeframes)
    3. Price above/below key EMAs
    4. Recent S/R breakout
    5. Volume confirmation
    6. MACD alignment
    7. Stochastic crossover
    8. Trend strength (ADX)
    
    Returns:
        (confluence_score, should_bypass_rsi, reasons)
    """
    confluence_score = 0
    reasons = []
    
    if df is None or len(df) < 60:
        return 0, False, []
    
    close = df['close']
    current_price = close.iloc[-1]
    
    # Calculate indicators
    ema9 = calc_ema(close, 9)
    ema21 = calc_ema(close, 21)
    ema50 = calc_ema(close, 50)
    
    ema9_val = ema9.iloc[-1]
    ema21_val = ema21.iloc[-1]
    ema50_val = ema50.iloc[-1] if len(ema50) > 0 else ema21_val
    
    stoch_k, stoch_d = calc_stochastic(df)
    stoch_k_val = stoch_k.iloc[-1] if len(stoch_k) > 0 else 50
    stoch_k_prev = stoch_k.iloc[-2] if len(stoch_k) > 1 else stoch_k_val
    stoch_d_val = stoch_d.iloc[-1] if len(stoch_d) > 0 else 50
    
    macd = MACD(close)
    macd_line = macd.macd().iloc[-1] if len(macd.macd()) > 0 else 0
    macd_signal = macd.macd_signal().iloc[-1] if len(macd.macd_signal()) > 0 else 0
    macd_histogram = macd.macd_diff().iloc[-1] if len(macd.macd_diff()) > 0 else 0
    macd_hist_prev = macd.macd_diff().iloc[-2] if len(macd.macd_diff()) > 1 else 0
    
    adx_indicator = ADXIndicator(df['high'], df['low'], close)
    adx_val = adx_indicator.adx().iloc[-1] if len(adx_indicator.adx()) > 0 else 20
    di_plus = adx_indicator.adx_pos().iloc[-1] if len(adx_indicator.adx_pos()) > 0 else 0
    di_minus = adx_indicator.adx_neg().iloc[-1] if len(adx_indicator.adx_neg()) > 0 else 0
    
    # Recent price action
    price_5_ago = close.iloc[-6] if len(close) > 5 else current_price
    momentum_pct = (current_price - price_5_ago) / price_5_ago * 100
    
    # Volume check
    volume = df['volume'].iloc[-1] if 'volume' in df else 0
    avg_volume = df['volume'].rolling(20).mean().iloc[-1] if 'volume' in df else 0
    volume_spike = volume > avg_volume * 1.3 if avg_volume > 0 else False
    
    # Check higher timeframes if available
    htf_aligned = False
    if df_1h is not None and len(df_1h) > 20:
        htf_ema9 = calc_ema(df_1h['close'], 9).iloc[-1]
        htf_ema21 = calc_ema(df_1h['close'], 21).iloc[-1]
        if direction == "LONG":
            htf_aligned = htf_ema9 > htf_ema21 and df_1h['close'].iloc[-1] > htf_ema21
        else:
            htf_aligned = htf_ema9 < htf_ema21 and df_1h['close'].iloc[-1] < htf_ema21
    
    if direction == "LONG":
        # 1. EMA Stack (Perfect alignment: 9 > 21 > 50)
        if ema9_val > ema21_val > ema50_val:
            confluence_score += 15
            reasons.append("EMA_STACK_PERFECT")
        elif ema9_val > ema21_val:
            confluence_score += 8
            reasons.append("EMA_STACK_PARTIAL")
        
        # 2. Price above key EMAs
        if current_price > ema9_val and current_price > ema21_val:
            confluence_score += 10
            reasons.append("PRICE_ABOVE_EMAS")
        elif current_price > ema21_val:
            confluence_score += 5
            reasons.append("PRICE_ABOVE_EMA21")
        
        # 3. MACD bullish
        if macd_line > macd_signal and macd_histogram > 0:
            confluence_score += 10
            reasons.append("MACD_BULLISH")
            # Bonus for rising histogram
            if macd_histogram > macd_hist_prev:
                confluence_score += 5
                reasons.append("MACD_RISING")
        
        # 4. Stochastic bullish crossover
        if stoch_k_val > stoch_d_val and stoch_k_val > stoch_k_prev:
            confluence_score += 8
            reasons.append("STOCH_BULLISH_CROSS")
        
        # 5. Strong trend (ADX > 25 with +DI > -DI)
        if adx_val > 25 and di_plus > di_minus:
            confluence_score += 10
            reasons.append("ADX_STRONG_TREND")
        
        # 6. Positive momentum
        if momentum_pct > 0.3:
            confluence_score += 8
            reasons.append("MOMENTUM_POSITIVE")
        
        # 7. Volume confirmation
        if volume_spike:
            confluence_score += 7
            reasons.append("VOLUME_SPIKE")
        
        # 8. Higher timeframe alignment
        if htf_aligned:
            confluence_score += 12
            reasons.append("HTF_ALIGNED")
        
        # 9. Price breaking above recent highs (wave breakout)
        recent_high = df['high'].iloc[-10:-1].max() if len(df) > 10 else current_price
        if current_price > recent_high:
            confluence_score += 10
            reasons.append("BREAKING_HIGHS")
    
    else:  # SHORT
        # 1. EMA Stack (Perfect alignment: 9 < 21 < 50)
        if ema9_val < ema21_val < ema50_val:
            confluence_score += 15
            reasons.append("EMA_STACK_PERFECT")
        elif ema9_val < ema21_val:
            confluence_score += 8
            reasons.append("EMA_STACK_PARTIAL")
        
        # 2. Price below key EMAs
        if current_price < ema9_val and current_price < ema21_val:
            confluence_score += 10
            reasons.append("PRICE_BELOW_EMAS")
        elif current_price < ema21_val:
            confluence_score += 5
            reasons.append("PRICE_BELOW_EMA21")
        
        # 3. MACD bearish
        if macd_line < macd_signal and macd_histogram < 0:
            confluence_score += 10
            reasons.append("MACD_BEARISH")
            # Bonus for falling histogram
            if macd_histogram < macd_hist_prev:
                confluence_score += 5
                reasons.append("MACD_FALLING")
        
        # 4. Stochastic bearish crossover
        if stoch_k_val < stoch_d_val and stoch_k_val < stoch_k_prev:
            confluence_score += 8
            reasons.append("STOCH_BEARISH_CROSS")
        
        # 5. Strong trend (ADX > 25 with -DI > +DI)
        if adx_val > 25 and di_minus > di_plus:
            confluence_score += 10
            reasons.append("ADX_STRONG_TREND")
        
        # 6. Negative momentum
        if momentum_pct < -0.3:
            confluence_score += 8
            reasons.append("MOMENTUM_NEGATIVE")
        
        # 7. Volume confirmation
        if volume_spike:
            confluence_score += 7
            reasons.append("VOLUME_SPIKE")
        
        # 8. Higher timeframe alignment
        if htf_aligned:
            confluence_score += 12
            reasons.append("HTF_ALIGNED")
        
        # 9. Price breaking below recent lows (wave breakdown)
        recent_low = df['low'].iloc[-10:-1].min() if len(df) > 10 else current_price
        if current_price < recent_low:
            confluence_score += 10
            reasons.append("BREAKING_LOWS")
    
    # v8.9.23: Fibonacci Sweet Spot confluence (score booster, not filter)
    try:
        fib718 = FibonacciSweetSpot.find_sweet_spot_zones(df, lookback=50)
        if direction == "LONG" and fib718.get('near_bullish_zone'):
            confluence_score += 10
            reasons.append("FIB_718_SUPPORT")
        elif direction == "LONG" and fib718.get('in_golden_zone'):
            confluence_score += 5
            reasons.append("FIB_GOLDEN_ZONE")
        elif direction == "SHORT" and fib718.get('near_bearish_zone'):
            confluence_score += 10
            reasons.append("FIB_718_RESISTANCE")
    except Exception:
        pass
    
    # Determine if we should bypass RSI filter
    # v8.9.19: Lowered threshold from 55 to 45 for better signal generation in trending markets
    CONFLUENCE_BYPASS_THRESHOLD = 55  # v8.9.20: Restored strict threshold
    should_bypass = confluence_score >= CONFLUENCE_BYPASS_THRESHOLD
    
    return confluence_score, should_bypass, reasons


# ================================
# ENTRY SIGNAL GENERATION (15min)
# ================================
def generate_entry_signal(symbol, df, trend, df_htf=None, macro_data=None, df_1h=None, quant_bias=0):
    if df is None or len(df) < 50:
        return None
    
    close = df['close']
    current_price = close.iloc[-1]
    
    rsi = calc_rsi(close)
    rsi_val = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2] if len(rsi) > 1 else rsi_val
    
    stoch_k, stoch_d = calc_stochastic(df)
    stoch_k_val = stoch_k.iloc[-1]
    
    bb_low, bb_mid, bb_high = calc_bollinger(close)
    bb_low_val = bb_low.iloc[-1]
    bb_high_val = bb_high.iloc[-1]
    bb_mid_val = bb_mid.iloc[-1]
    
    atr = calc_atr(df)
    atr_val = atr.iloc[-1]
    
    ema9 = calc_ema(close, 9).iloc[-1]
    ema21 = calc_ema(close, 21).iloc[-1]
    
    long_score = 0
    short_score = 0
    long_signals = []
    short_signals = []
    
    # v8.9.3: QUANT BIAS INTEGRATION
    # Add mathematical analysis score to entry signal generation
    if quant_bias >= 15:
        long_score += quant_bias  # +15 to +25 for strong LONG bias
        long_signals.append(f"QUANT_LONG_{quant_bias}")
    elif quant_bias <= -15:
        short_score += abs(quant_bias)  # +15 to +25 for strong SHORT bias
        short_signals.append(f"QUANT_SHORT_{abs(quant_bias)}")
    
    # 4H Macro confluence (if provided)
    if macro_data:
        macro_bias = macro_data.get('bias', 'NEUTRAL')
        macro_signals = macro_data.get('signals', [])
        
        if macro_bias in ['STRONG_BULL', 'BULL']:
            long_score += 15
            long_signals.extend([s for s in macro_signals if 'BULL' in s or 'UP' in s][:2])
        elif macro_bias in ['STRONG_BEAR', 'BEAR']:
            short_score += 15
            short_signals.extend([s for s in macro_signals if 'BEAR' in s or 'DOWN' in s][:2])
        
        # BB Squeeze bonus - potential breakout
        if macro_data.get('squeeze'):
            long_signals.append("4H_SQUEEZE") if long_score > short_score else short_signals.append("4H_SQUEEZE")
    
    # 1H Trend
    if trend in ["STRONG_BULL", "BULL"]:
        long_score += 25
        long_signals.append(f"1H_{trend}")
    elif trend in ["STRONG_BEAR", "BEAR"]:
        short_score += 25
        short_signals.append(f"1H_{trend}")
    
    if rsi_val <= RSI_OVERSOLD:
        long_score += 20
        long_signals.append("RSI_OVERSOLD")
    elif rsi_val < 40 and rsi_val > rsi_prev:
        long_score += 10
        long_signals.append("RSI_RECOVERY")
    elif rsi_val >= RSI_OVERBOUGHT:
        short_score += 20
        short_signals.append("RSI_OVERBOUGHT")
    elif rsi_val > 60 and rsi_val < rsi_prev:
        short_score += 10
        short_signals.append("RSI_EXHAUSTION")
    
    if stoch_k_val < 20:
        long_score += 15
        long_signals.append("STOCH_OVERSOLD")
    elif stoch_k_val > 80:
        short_score += 15
        short_signals.append("STOCH_OVERBOUGHT")
    
    if current_price <= bb_low_val * 1.01:
        long_score += 15
        long_signals.append("BB_LOWER")
    elif current_price >= bb_high_val * 0.99:
        short_score += 15
        short_signals.append("BB_UPPER")
    
    if ema9 > ema21:
        long_score += 10
        long_signals.append("EMA_BULL")
    else:
        short_score += 10
        short_signals.append("EMA_BEAR")
    
    bullish_candle = close.iloc[-1] > df['open'].iloc[-1]
    bearish_candle = close.iloc[-1] < df['open'].iloc[-1]
    candle_body = abs(close.iloc[-1] - df['open'].iloc[-1])
    candle_range = df['high'].iloc[-1] - df['low'].iloc[-1]
    strong_candle = candle_body > candle_range * 0.6 if candle_range > 0 else False
    
    if bullish_candle and strong_candle:
        long_score += 10
        long_signals.append("STRONG_BULL_CANDLE")
    elif bearish_candle and strong_candle:
        short_score += 10
        short_signals.append("STRONG_BEAR_CANDLE")
    
    # Support/Resistance Breakout Detection
    recent_lows = df['low'].iloc[-BREAKOUT_LOOKBACK:-1]
    recent_highs = df['high'].iloc[-BREAKOUT_LOOKBACK:-1]
    support_level = recent_lows.min()
    resistance_level = recent_highs.max()
    
    # Breakout below support = SHORT signal
    if current_price < support_level * (1 - BREAKOUT_THRESHOLD):
        short_score += 20
        short_signals.append("SUPPORT_BREAK")
    
    # Breakout above resistance = LONG signal
    if current_price > resistance_level * (1 + BREAKOUT_THRESHOLD):
        long_score += 20
        long_signals.append("RESISTANCE_BREAK")
    
    # Momentum confirmation (price moving in direction)
    price_change_5 = (current_price - close.iloc[-6]) / close.iloc[-6]
    if price_change_5 < -0.01:  # -1% drop
        short_score += 10
        short_signals.append("MOMENTUM_DOWN")
    elif price_change_5 > 0.01:  # +1% rise
        long_score += 10
        long_signals.append("MOMENTUM_UP")
    
    # Note: sl_distance is now calculated dynamically below based on volatility (v8.2)
    tp1_distance = atr_val * 2.5
    tp2_distance = atr_val * 4
    tp3_distance = atr_val * 6
    
    signal = None
    base_direction = None
    base_score = 0
    base_signals = []
    
    # Candle confirmation: require price moving in signal direction
    # For SHORT: last candle must be bearish OR momentum down (not rallying up)
    # For LONG: last candle must be bullish OR momentum up (not dropping)
    long_candle_ok = bullish_candle or price_change_5 > 0
    short_candle_ok = bearish_candle or price_change_5 < 0
    
    # STRICTER SHORT FILTERS (v8.1 improvement)
    # 1. Require BOTH 4H BEAR + 1H BEAR for SHORT (no NEUTRAL allowed)
    # 2. Require momentum reversal confirmation (price dropping + structural confirmation)
    macro_confirms_short = False
    macro_bias = 'NEUTRAL'
    if macro_data:
        macro_bias = macro_data.get('bias', 'NEUTRAL')
        macro_confirms_short = macro_bias in ['STRONG_BEAR', 'BEAR']
    
    # 1H trend must also be BEAR for SHORT
    trend_confirms_short = trend in ['STRONG_BEAR', 'BEAR']
    
    # Full structure alignment: BOTH 4H AND 1H must be BEAR
    full_bear_alignment = macro_confirms_short and trend_confirms_short
    
    # Momentum reversal: price must be falling significantly
    momentum_reversal_short = price_change_5 < -0.008  # At least -0.8% drop (stricter)
    
    # MACD confirmation: check if MACD is bearish
    from ta.trend import MACD as MACD_Indicator
    macd_indicator = MACD_Indicator(close)
    macd_line = macd_indicator.macd().iloc[-1]
    macd_signal = macd_indicator.macd_signal().iloc[-1]
    macd_bearish = macd_line < macd_signal  # MACD below signal = bearish
    
    # For SHORT: require full alignment (4H+1H BEAR) AND MACD bearish confirmation
    # v8.1: Stricter - MACD must confirm, momentum reversal adds bonus but not required
    short_confirmed = short_candle_ok and full_bear_alignment and macd_bearish
    
    # v8.2: PULLBACK COMPLETION FILTER - wait for pullback to complete before entry
    pullback_ok_short, pullback_reason_short = pullback_complete_short(df, ema21)
    pullback_ok_long, pullback_reason_long = pullback_complete_long(df, ema21)
    
    # v8.2: VOLUME CONFIRMATION
    volume_ok, volume_reason = check_volume_confirmation(df, "SHORT" if short_score > long_score else "LONG")
    
    # v8.2: VOLATILITY-ADJUSTED STOP LOSS
    vix_value = macro_state.get('vix_value', 20)
    atr_multiplier = get_volatility_adjusted_atr_multiplier(df_htf, vix_value)
    sl_distance = atr_val * atr_multiplier  # Dynamic based on volatility
    
    # For LONG in BEAR market: allow if RSI <= RSI_OVERSOLD OR quant_bias >= 15 (v8.9.3)
    long_in_bear_ok = rsi_val <= RSI_OVERSOLD
    quant_counter_trend = quant_bias >= 15  # v8.9.3: Quant-confirmed counter-trend
    
    # v8.9.4: Check if counter-trend is auto-disabled due to poor performance
    ct_stats = get_counter_trend_stats()
    if ct_stats["ct_disabled"]:
        quant_counter_trend = False  # Disable counter-trend signals
        if quant_bias >= 15:
            print(f"  ⚠️ {symbol}: Counter-trend DISABLED (win rate: {ct_stats['ct_win_rate']}%, {ct_stats['ct_total']} trades)")
    
    # v8.9.6: Counter-trend requires MANDATORY Candle Reversal pattern (The Rumers style)
    # Both quant_counter_trend AND long_in_bear_ok require candle reversal confirmation
    # RSI must be <= 40 (stricter) AND bullish candle reversal pattern must be present
    has_candle_reversal = False
    candle_rev_pattern = None
    
    # v8.9.24: REBOUND ENTRY REFINER - Check for score/RR boost in BEAR markets
    has_bullish_divergence = detect_rsi_divergence(df_1h, direction="LONG") if df_1h is not None else False
    rebound_refiner_result = None
    atr_ratio = 1.0
    rebound_score_boost = 0
    rebound_rr_improvement = 1.0
    
    if trend in ["BEAR", "STRONG_BEAR"]:
        # Calculate ATR ratio (current vs average)
        try:
            atr_series = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
            atr_current = atr_series.iloc[-1]
            atr_avg = atr_series.tail(20).mean()
            atr_ratio = atr_current / atr_avg if atr_avg > 0 else 1.0
        except:
            atr_ratio = 1.0
        
        # Check for bullish 5m close
        bullish_candle_close = df['close'].iloc[-1] > df['open'].iloc[-1]
        
        rebound_ctx = ReboundRefinerContext(
            market_regime="BEAR",
            rsi=rsi_val,
            atr_ratio=atr_ratio,
            has_bullish_divergence=has_bullish_divergence,
            candle_reversal=long_candle_ok,
            bullish_candle_close=bullish_candle_close
        )
        rebound_refiner_result = evaluate_rebound_refiner(rebound_ctx)
        
        if rebound_refiner_result.active:
            rebound_score_boost = rebound_refiner_result.score_boost
            rebound_rr_improvement = rebound_refiner_result.rr_improvement
            print(f"  📈 {symbol}: REBOUND REFINER +{rebound_score_boost}pts, R:R×{rebound_rr_improvement:.2f} ({rebound_refiner_result.reason})")
            long_signals.append(f"REBOUND_BOOST+{rebound_score_boost}")
    
    # Legacy compatibility (empty result for non-BEAR)
    scalp_rebound_result = None
    
    # v8.9.6: Check for candle reversal pattern for ANY counter-trend LONG (quant or extreme oversold)
    counter_trend_possible = (quant_counter_trend or long_in_bear_ok) and rsi_val <= 40
    if counter_trend_possible:
        # Check for bullish reversal patterns in current timeframe
        # Patterns: Bullish Engulfing, Hammer (lower wick > 2x body), Dragonfly Doji
        open_price = df['open'].iloc[-1]
        close_price = df['close'].iloc[-1]
        high_price = df['high'].iloc[-1]
        low_price = df['low'].iloc[-1]
        prev_open = df['open'].iloc[-2]
        prev_close = df['close'].iloc[-2]
        
        body = abs(close_price - open_price)
        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price
        total_range = high_price - low_price
        
        is_bullish = close_price > open_price
        prev_bearish = prev_close < prev_open
        
        # Bullish Engulfing: current bullish candle engulfs previous bearish
        if is_bullish and prev_bearish and close_price > prev_open and open_price < prev_close:
            has_candle_reversal = True
            candle_rev_pattern = "BULLISH_ENGULFING"
        
        # Hammer: lower wick > 2x body, small upper wick
        elif is_bullish and lower_wick > body * 2 and upper_wick < body * 0.5 and body > 0:
            has_candle_reversal = True
            candle_rev_pattern = "HAMMER"
        
        # Dragonfly Doji: very small body, long lower wick
        elif total_range > 0 and body < total_range * 0.1 and lower_wick > total_range * 0.6:
            has_candle_reversal = True
            candle_rev_pattern = "DRAGONFLY_DOJI"
        
        # Morning Star (3 candle): bearish, small body, bullish closing above midpoint
        if len(df) >= 3 and not has_candle_reversal:
            candle_3_ago = df.iloc[-3]
            candle_2_ago = df.iloc[-2]
            candle_1 = df.iloc[-1]
            
            first_bearish = candle_3_ago['close'] < candle_3_ago['open']
            second_small = abs(candle_2_ago['close'] - candle_2_ago['open']) < abs(candle_3_ago['close'] - candle_3_ago['open']) * 0.5
            third_bullish = candle_1['close'] > candle_1['open']
            midpoint = (candle_3_ago['open'] + candle_3_ago['close']) / 2
            
            if first_bearish and second_small and third_bullish and candle_1['close'] > midpoint:
                has_candle_reversal = True
                candle_rev_pattern = "MORNING_STAR"
    
    # v8.9.6: Block ALL counter-trend LONGs without candle reversal confirmation
    # This applies to both quant_counter_trend AND long_in_bear_ok (extreme oversold)
    if not has_candle_reversal:
        if quant_counter_trend:
            quant_counter_trend = False
            if rsi_val <= 40:
                print(f"  ⚠️ {symbol}: Counter-trend blocked - waiting for candle reversal (RSI={rsi_val:.0f})")
            else:
                print(f"  ⚠️ {symbol}: Counter-trend blocked - RSI too high ({rsi_val:.0f} > 40)")
        if long_in_bear_ok:
            long_in_bear_ok = False  # v8.9.6: Also block extreme oversold without reversal
            print(f"  ⚠️ {symbol}: Extreme oversold blocked - waiting for candle reversal (RSI={rsi_val:.0f})")
    
    # v8.9.22: OB/FVG ZONE CHECK - Counter-trend MUST be at key zone
    # Professional rules: "Entry must be at Order Block or FVG zone"
    is_at_key_zone = False
    key_zone_type = None
    if has_candle_reversal and (quant_counter_trend or long_in_bear_ok):
        zone_check = OrderBlocks.is_price_at_key_zone(df, 'LONG', tolerance_pct=0.8)
        is_at_key_zone = zone_check.get('is_at_zone', False)
        key_zone_type = zone_check.get('zone_type')
        
        if not is_at_key_zone:
            if quant_counter_trend:
                quant_counter_trend = False
                print(f"  ⚠️ {symbol}: Counter-trend blocked - not at OB/FVG zone (need key zone for counter-trend)")
            if long_in_bear_ok:
                long_in_bear_ok = False
                print(f"  ⚠️ {symbol}: Extreme oversold blocked - not at OB/FVG zone")
    
    # v8.9.22: 4H CHoCH CONFIRMATION - Counter-trend MUST show structure change on 4H
    # Professional rules: "4H timeframe must confirm potential reversal (CHoCH)"
    # FAIL-SAFE: On any error, block counter-trend (don't allow without confirmation)
    has_4h_choch = False
    if is_at_key_zone and has_candle_reversal and (quant_counter_trend or long_in_bear_ok):
        try:
            df_4h_check = fetch_ohlcv(symbol, '4h', limit=100)
            if df_4h_check is not None and len(df_4h_check) >= 50:
                choch_result = MarketStructure.detect_choch_4h(df_4h_check)
                has_4h_choch = choch_result.get('bullish_choch', False)
                
                if not has_4h_choch:
                    if quant_counter_trend:
                        quant_counter_trend = False
                        print(f"  ⚠️ {symbol}: Counter-trend blocked - no 4H CHoCH (need structure change on 4H)")
                    if long_in_bear_ok:
                        long_in_bear_ok = False
                        print(f"  ⚠️ {symbol}: Extreme oversold blocked - no 4H CHoCH confirmation")
                else:
                    print(f"  ✅ {symbol}: 4H CHoCH confirmed for counter-trend LONG")
            else:
                # FAIL-SAFE: Insufficient data = block counter-trend
                if quant_counter_trend:
                    quant_counter_trend = False
                if long_in_bear_ok:
                    long_in_bear_ok = False
                print(f"  ⚠️ {symbol}: Counter-trend blocked - insufficient 4H data for CHoCH check")
        except Exception as e:
            # FAIL-SAFE: Any error = block counter-trend (never allow without CHoCH confirmation)
            if quant_counter_trend:
                quant_counter_trend = False
            if long_in_bear_ok:
                long_in_bear_ok = False
            print(f"  ⚠️ {symbol}: Counter-trend blocked - 4H CHoCH check failed: {e}")
    
    # v8.9.3: Lower MIN_SCORE for quant-confirmed counter-trend
    # Very strong quant (>=25): min_score = 25
    # Strong quant (>=20): min_score = 35, moderate quant (>=15): min_score = 50
    if quant_bias >= 25 and quant_counter_trend:
        effective_min_score_long = 25  # Very strong quant
    elif quant_bias >= 20 and quant_counter_trend:
        effective_min_score_long = 35  # Strong quant confirmation
    elif quant_bias >= 15 and quant_counter_trend:
        effective_min_score_long = 50  # Moderate quant confirmation
    else:
        effective_min_score_long = MIN_SCORE
    
    # v8.9.3: For very strong quant (>=25), relax candle_ok requirement - trying to catch early reversal
    # v8.9.4: But still require has_ta_confirmation
    effective_candle_ok = long_candle_ok or (quant_bias >= 25 and quant_counter_trend)
    
    # v8.9.5: RELAXED SHORT REQUIREMENTS
    # RSI overbought (>= 65) is strong enough signal on its own in BEAR market
    rsi_overbought = rsi_val >= RSI_OVERBOUGHT  # >= 70
    rsi_moderately_overbought = rsi_val >= 65
    
    # v8.9.5: Lower MIN_SCORE for SHORT when RSI is overbought
    if rsi_overbought and trend_confirms_short:
        effective_min_score_short = 45  # RSI > 70 in BEAR = strong SHORT signal
    elif rsi_moderately_overbought and trend_confirms_short:
        effective_min_score_short = 55  # RSI > 65 in BEAR = moderate SHORT signal
    else:
        effective_min_score_short = MIN_SCORE
    
    # v8.9.5: Relaxed short_confirmed - require EITHER full alignment OR RSI overbought
    short_confirmed_relaxed = short_candle_ok and (full_bear_alignment or rsi_overbought)
    
    # v8.9.24: Apply rebound score boost BEFORE direction decision
    if rebound_score_boost > 0:
        long_score += rebound_score_boost
    
    if long_score >= effective_min_score_long and effective_candle_ok:
        # Allow LONG in BULL/NEUTRAL trend, OR in BEAR if extreme oversold OR quant confirms counter-trend
        if trend in ["STRONG_BULL", "BULL", "NEUTRAL"] or (trend in ["STRONG_BEAR", "BEAR"] and (long_in_bear_ok or quant_counter_trend)):
            base_direction = "LONG"
            base_score = long_score
            base_signals = long_signals
            if trend in ["STRONG_BEAR", "BEAR"]:
                if long_in_bear_ok:
                    base_signals.append("CONTRARIAN_OVERSOLD")
                    if candle_rev_pattern:
                        base_signals.append(f"CT_{candle_rev_pattern}")
                if quant_counter_trend:
                    base_signals.append(f"QUANT_COUNTER_TREND_{quant_bias}")
                    if candle_rev_pattern and "CT_" not in " ".join(base_signals):
                        base_signals.append(f"CT_{candle_rev_pattern}")
                # v8.9.22: Add key zone and 4H CHoCH confirmation to signals
                if key_zone_type:
                    base_signals.append(f"ZONE_{key_zone_type}")
                if has_4h_choch:
                    base_signals.append("4H_CHOCH_CONFIRMED")
    elif short_score >= effective_min_score_short and short_score > long_score and trend_confirms_short and short_confirmed_relaxed:
        # v8.9.5: Relaxed SHORT - removed pullback_ok requirement, relaxed short_confirmed
        base_direction = "SHORT"
        base_score = short_score
        base_signals = short_signals
        if rsi_overbought:
            base_signals.append("RSI_OVERBOUGHT_ENTRY")
        if momentum_reversal_short:
            base_signals.append("MOMENTUM_REVERSAL")
        if full_bear_alignment:
            base_signals.append("4H_1H_ALIGNED")
        if macd_bearish:
            base_signals.append("MACD_BEARISH")
        if pullback_ok_short:
            base_signals.append("PULLBACK_COMPLETE")
        if volume_ok:
            base_signals.append(volume_reason)
    
    if base_direction:
        # v8.8: 1H RSI PRE-FILTER (The Rumers inspired)
        # v8.9.3: Skip RSI filter for very strong quant counter-trend (quant_bias >= 25)
        # v8.9.4: Only skip if quant_counter_trend is still enabled (not disabled by safety checks)
        # v8.9.19: NEW! Multi-Indicator Confluence Bypass - when indicators align perfectly
        skip_rsi_filter = (base_direction == "LONG" and quant_bias >= 25 and quant_counter_trend)
        
        # v8.9.19: Check Multi-Indicator Confluence for RSI bypass
        confluence_score, confluence_bypass, confluence_reasons = check_multi_indicator_confluence(
            df, df_1h, df_htf, base_direction
        )
        if confluence_bypass and not skip_rsi_filter:
            skip_rsi_filter = True
            print(f"  🎯 {symbol}: Confluence bypass activated! Score={confluence_score} ({', '.join(confluence_reasons[:3])})")
        
        rsi_filter_ok, rsi_filter_reason = check_1h_rsi_prefilter(df_1h, base_direction, lookback=6)
        if not rsi_filter_ok and not skip_rsi_filter:
            # v8.9.19: Show confluence score even when blocked
            if confluence_score >= 40:
                print(f"  ⚠️ {symbol}: {base_direction} almost bypassed (confluence={confluence_score}/55) - {rsi_filter_reason}")
            else:
                print(f"  ❌ {symbol}: {base_direction} blocked by 1H RSI filter - {rsi_filter_reason}")
            return None
        if skip_rsi_filter and not rsi_filter_ok:
            if confluence_bypass:
                rsi_filter_reason = f"CONFLUENCE_BYPASS_{confluence_score}"  # Mark as confluence-bypassed
            else:
                rsi_filter_reason = "QUANT_RSI_BYPASS"  # Mark as quant-bypassed
        
        # v8.9.22: ORDER FLOW FILTER
        # Blocks fake breakouts (big range + low volume = trap)
        of_filter_ok, of_filter_reason = order_flow_filter(df, base_direction)
        if not of_filter_ok:
            print(f"  ❌ {symbol}: {base_direction} blocked by Order Flow filter - {of_filter_reason}")
            return None
        
        integrated = get_integrated_score(symbol, df_htf, df, base_direction, base_score)
        
        final_score = integrated['final_score']
        all_signals = base_signals + integrated['signals']
        
        # v8.9.19: Add confluence signals
        if confluence_bypass:
            all_signals.extend([f"CFN_{r}" for r in confluence_reasons[:3]])  # Add top 3 confluence reasons
            final_score += min(10, confluence_score // 10)  # Bonus points for confluence (max +10)
        
        # v8.8: Add 1H RSI filter signal
        if "1H_RSI" in rsi_filter_reason or "CONFLUENCE" in rsi_filter_reason or "QUANT" in rsi_filter_reason:
            all_signals.append(rsi_filter_reason.split(" ")[0])  # Add e.g. "1H_RSI_OVERSOLD_32" or "CONFLUENCE_BYPASS_60"
        
        # v8.9.22: Add Order Flow signal
        if of_filter_reason in ["OF_ABSORPTION_REJECTION", "OF_FOLLOW_THROUGH"]:
            all_signals.append(of_filter_reason)
        confidence = integrated['confidence']
        
        # v8.9.3: Use effective MIN_SCORE for quant counter-trend LONG
        # v8.9.5: Use effective MIN_SCORE for relaxed SHORT
        if base_direction == "LONG" and quant_counter_trend:
            effective_final_min = effective_min_score_long
        elif base_direction == "SHORT":
            effective_final_min = effective_min_score_short
        else:
            effective_final_min = MIN_SCORE
        
        if final_score >= effective_final_min:
            # v8.9.20: ConsolidationGuard - Block signals in range/consolidation
            is_consol, consol_reasons = is_consolidating(df, current_price, signal_data={
                'wave_score': integrated.get('wave_score', 50),
                'confluence_score': confluence_score
            })
            if is_consol:
                print(f"  🔄 {symbol}: CONSOLIDATION blocked - {', '.join(consol_reasons)}")
                return None
            
            # v8.3: Determine entry type (MARKET vs LIMIT)
            atr_pct = (atr_val / current_price) * 100 if current_price > 0 else 0
            entry_type, entry_reason = determine_entry_type(rsi_val, trend, atr_pct, all_signals, base_direction)
            
            # v8.7: TP1 pagal S/R zonas
            tp1_sr, tp1_used_sr = get_tp1_from_sr_zone(current_price, base_direction, df, atr_val)
            if tp1_used_sr:
                all_signals.append("TP1_SR_ZONE")
            
            # v8.9: Calculate confirmation count for dynamic leverage
            confirmation_count = len([s for s in all_signals if not s.startswith('4H_') and not s.startswith('1H_') and 'CONFLICT' not in s])
            
            # v8.9: Get ML confidence if available
            ml_confidence = None
            try:
                if ml_predictor and hasattr(ml_predictor, 'predict_signal'):
                    ml_result = ml_predictor.predict_signal({
                        'direction': base_direction,
                        'score': final_score,
                        'rsi': rsi_val,
                        'trend': trend,
                        'signals': all_signals
                    })
                    if ml_result and 'confidence' in ml_result:
                        ml_confidence = ml_result['confidence']
            except:
                pass
            
            if base_direction == "LONG":
                signal = {
                    "symbol": symbol,
                    "direction": "LONG",
                    "score": final_score,
                    "base_score": base_score,
                    "signals": all_signals,
                    "price": current_price,
                    "sl": current_price - sl_distance,
                    "tp1": tp1_sr,
                    "tp2": current_price + tp2_distance,
                    "tp3": current_price + tp3_distance,
                    "atr": atr_val,
                    "rsi": rsi_val,
                    "trend": trend,
                    "confidence": confidence,
                    "modules_used": integrated['modules_used'],
                    "entry_type": entry_type,
                    "entry_reason": entry_reason,
                    "confirmation_count": confirmation_count,
                    "ml_confidence": ml_confidence,
                    "quant_bias": integrated.get('quant_bias', 0),  # v8.9.2: For counter-trend
                    "confluence_score": confluence_score,  # v8.9.19: Multi-indicator confluence
                    "atr_ratio": atr_ratio,  # v8.9.23: For RR Engine
                    "is_countertrend": trend in ["BEAR", "STRONG_BEAR"],  # v8.9.23
                    "rebound_boost": rebound_score_boost,  # v8.9.24: Entry refiner score boost
                    "rebound_rr_mult": rebound_rr_improvement,  # v8.9.24: R:R improvement
                    "time": datetime.now(timezone.utc)
                }
            else:
                signal = {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "score": final_score,
                    "base_score": base_score,
                    "signals": all_signals,
                    "price": current_price,
                    "sl": current_price + sl_distance,
                    "tp1": tp1_sr,
                    "tp2": current_price - tp2_distance,
                    "tp3": current_price - tp3_distance,
                    "atr": atr_val,
                    "rsi": rsi_val,
                    "trend": trend,
                    "confidence": confidence,
                    "modules_used": integrated['modules_used'],
                    "entry_type": entry_type,
                    "entry_reason": entry_reason,
                    "confirmation_count": confirmation_count,
                    "ml_confidence": ml_confidence,
                    "quant_bias": integrated.get('quant_bias', 0),  # v8.9.2: For counter-trend
                    "confluence_score": confluence_score,  # v8.9.19: Multi-indicator confluence
                    "atr_ratio": atr_ratio,  # v8.9.23: For RR Engine
                    "is_countertrend": False,  # SHORT is with-trend in BEAR
                    "rebound_boost": 0,  # v8.9.24: No boost for SHORT
                    "rebound_rr_mult": 1.0,  # v8.9.24: No improvement
                    "time": datetime.now(timezone.utc)
                }
    
    return signal

# ================================
# ENTRY TYPE RECOMMENDATION (MARKET vs LIMIT)
# ================================
def determine_entry_type(rsi_val: float, trend: str, atr_pct: float, signals: list, direction: str) -> tuple:
    """
    Nustatyti rekomenduojamą entry tipą: MARKET arba LIMIT
    
    MARKET rekomenduojamas kai:
    - Ekstremalus RSI (< 25 arba > 75) - stiprus momentumas
    - Strong trend alignment - kaina greitai juda
    - Breakout signalai (STRUCTURE, MOMENTUM_REVERSAL)
    - Didelis volatilumas (ATR > 2.5% kainos)
    
    LIMIT rekomenduojamas kai:
    - Normalus RSI (25-75)
    - Konsolidacija arba range rinka
    - Galima palaukti geresnės kainos
    """
    reasons = []
    market_score = 0
    
    # RSI ekstremumai - stiprus momentumas, reikia greitai įeiti
    if direction == "LONG" and rsi_val < 25:
        market_score += 3
        reasons.append("RSI<25 oversold")
    elif direction == "SHORT" and rsi_val > 75:
        market_score += 3
        reasons.append("RSI>75 overbought")
    elif rsi_val < 30 or rsi_val > 70:
        market_score += 1
        reasons.append("RSI ekstremus")
    
    # Strong trend - kaina juda greitai
    if trend in ["STRONG_BULL", "STRONG_BEAR"]:
        market_score += 2
        reasons.append("Stiprus trendas")
    
    # Breakout/momentum signalai
    breakout_signals = ["MOMENTUM_REVERSAL", "STRUCTURE_BULL", "STRUCTURE_BEAR", "SFP", "LIQUIDITY_SWEEP"]
    if any(sig in signals for sig in breakout_signals):
        market_score += 2
        reasons.append("Breakout signalas")
    
    # Didelis volatilumas
    if atr_pct > 2.5:
        market_score += 1
        reasons.append("Aukštas volatilumas")
    
    # Pullback complete = galima limit, nes kaina jau stabilizavosi
    if "PULLBACK_COMPLETE" in signals:
        market_score -= 1
    
    # Range/consolidation signalai - limit geriau
    range_signals = ["RANGE_LOW", "RANGE_HIGH", "BB_LOW", "BB_HIGH"]
    if any(sig in signals for sig in range_signals):
        market_score -= 1
    
    # Sprendimas: >= 3 = MARKET, kitu atveju LIMIT
    if market_score >= 3:
        entry_type = "MARKET"
        reason = f"⚡ {', '.join(reasons[:2])}"
    else:
        entry_type = "LIMIT"
        reason = "💤 Stabili kaina, galima palaukti"
    
    return entry_type, reason


# ================================
# HOLD TIME CALCULATOR
# ================================
def calculate_hold_time(signal: dict) -> dict:
    """
    Apskaičiuoti rekomenduojamą laikymo laiką
    Pagrįsta: ATR volatility, TP atstumu, asset tipu
    Laikas rodomas Airijos laiku (GMT/IST)
    """
    import pytz
    ireland_tz = pytz.timezone('Europe/Dublin')
    current_time = signal.get('time', datetime.now(timezone.utc))
    symbol = signal['symbol']
    direction = signal['direction']
    entry = signal['price']
    tp1 = signal['tp1']
    tp2 = signal['tp2']
    tp3 = signal['tp3']
    atr = signal.get('atr', 0)
    
    # Asset speed factor (BTC lėčiausias, altcoins greitesni)
    speed_factors = {
        'PF_XBTUSD': 1.0,    # BTC - baseline
        'PF_ETHUSD': 1.2,    # ETH - 20% greičiau
        'PF_SOLUSD': 1.5,    # SOL - 50% greičiau
        'PF_XRPUSD': 1.4,    # XRP - 40% greičiau
    }
    speed = speed_factors.get(symbol, 1.0)
    
    # Apskaičiuoti TP atstumus procentais
    if direction == "LONG":
        tp1_pct = (tp1 - entry) / entry * 100
        tp2_pct = (tp2 - entry) / entry * 100
        tp3_pct = (tp3 - entry) / entry * 100
    else:
        tp1_pct = (entry - tp1) / entry * 100
        tp2_pct = (entry - tp2) / entry * 100
        tp3_pct = (entry - tp3) / entry * 100
    
    # Bazinis laikas pagal ATR ir TP atstumą
    # Vidutiniškai crypto juda ~0.5-1% per valandą volatility metu
    hourly_move_estimate = 0.7 * speed  # % per valandą
    
    # Laikas iki TP (valandos)
    hours_to_tp1 = max(1, tp1_pct / hourly_move_estimate)
    hours_to_tp2 = max(2, tp2_pct / hourly_move_estimate)
    hours_to_tp3 = max(4, tp3_pct / hourly_move_estimate)
    
    # Maksimalus rekomenduojamas laikymo laikas (iki TP2)
    max_hold_hours = min(48, hours_to_tp2 * 1.5)  # Ne daugiau 48h
    
    # Apskaičiuoti deadline laikus (konvertuoti į Airijos laiką)
    tp1_deadline_utc = current_time + timedelta(hours=hours_to_tp1)
    tp2_deadline_utc = current_time + timedelta(hours=hours_to_tp2)
    max_hold_deadline_utc = current_time + timedelta(hours=max_hold_hours)
    
    # Konvertuoti į Airijos laiką
    tp1_deadline = tp1_deadline_utc.astimezone(ireland_tz)
    tp2_deadline = tp2_deadline_utc.astimezone(ireland_tz)
    max_hold_deadline = max_hold_deadline_utc.astimezone(ireland_tz)
    
    return {
        'hours_to_tp1': round(hours_to_tp1, 1),
        'hours_to_tp2': round(hours_to_tp2, 1),
        'hours_to_tp3': round(hours_to_tp3, 1),
        'max_hold_hours': round(max_hold_hours, 1),
        'tp1_deadline': tp1_deadline,
        'tp2_deadline': tp2_deadline,
        'max_hold_deadline': max_hold_deadline,
        'timezone': 'Airijos laikas',
        'recommendation': f"Laikyti iki {max_hold_deadline.strftime('%H:%M')} (max {int(max_hold_hours)}h)"
    }

# ================================
# TELEGRAM NOTIFICATIONS
# ================================
async def send_telegram_signal(signal):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram not configured")
        return
    
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        
        asset_name = ASSET_NAMES.get(signal['symbol'], signal['symbol'])
        direction_emoji = "🟢" if signal['direction'] == "LONG" else "🔴"
        
        confidence = signal.get('confidence', 0.5)
        confidence_stars = "⭐" * min(5, int(confidence * 5) + 1)
        modules_used = signal.get('modules_used', 0)
        base_score = signal.get('base_score', signal['score'])
        
        ta_signals = [s for s in signal['signals'] if not any(x in s for x in ['STRUCTURE', 'OB', 'SFP', 'DIV', 'QUANT', 'SENTIMENT', 'WHALE', 'MC_', 'ARIMA', 'FIB'])]
        pro_signals = [s for s in signal['signals'] if any(x in s for x in ['STRUCTURE', 'OB', 'SFP', 'DIV', 'RANGE'])]
        ai_signals = [s for s in signal['signals'] if any(x in s for x in ['QUANT', 'SENTIMENT', 'WHALE', 'MC_', 'ARIMA', 'FIB', 'ACCUMULATION', 'DISTRIBUTION'])]
        
        # Calculate hold time recommendation
        hold_time = calculate_hold_time(signal)
        
        # v8.3: Entry type recommendation
        entry_type = signal.get('entry_type', 'LIMIT')
        entry_reason = signal.get('entry_reason', '')
        entry_emoji = "⚡" if entry_type == "MARKET" else "🎯"
        
        message = f"""
{direction_emoji} <b>PRO FUTURES SIGNAL</b> {direction_emoji}

<b>Asset:</b> {asset_name} (Perpetual)
<b>Direction:</b> {signal['direction']}
<b>Leverage:</b> {LEVERAGE}x

📊 <b>SCORE:</b> {signal['score']}/100 {confidence_stars}
<i>Base: {base_score} + AI/Quant: {signal['score'] - base_score}</i>
<i>Moduliai: {modules_used} | Confidence: {confidence*100:.0f}%</i>

💰 <b>ENTRY LEVELS:</b>
{entry_emoji} <b>Entry {entry_type}:</b> ${signal['price']:.2f}
<i>{entry_reason}</i>
<b>Stop Loss:</b> ${signal['sl']:.2f} ({abs((signal['sl']-signal['price'])/signal['price']*100):.1f}%)
<b>TP1:</b> ${signal['tp1']:.2f} ({abs((signal['tp1']-signal['price'])/signal['price']*100):.1f}%)
<b>TP2:</b> ${signal['tp2']:.2f}
<b>TP3:</b> ${signal['tp3']:.2f}

⏱️ <b>LAIKYMO LAIKAS (Airijos):</b>
<b>TP1:</b> ~{hold_time['hours_to_tp1']}h (iki {hold_time['tp1_deadline'].strftime('%H:%M')})
<b>TP2:</b> ~{hold_time['hours_to_tp2']}h (iki {hold_time['tp2_deadline'].strftime('%H:%M')})
<b>MAX:</b> {int(hold_time['max_hold_hours'])}h (iki {hold_time['max_hold_deadline'].strftime('%H:%M')})

📈 <b>TECHNICAL:</b>
Trend: {signal['trend']} | RSI: {signal['rsi']:.1f}
{', '.join(ta_signals[:4]) if ta_signals else 'N/A'}

🎯 <b>PRO STRATEGIES:</b>
{', '.join(pro_signals[:3]) if pro_signals else 'Standard TA'}

🤖 <b>AI + QUANT:</b>
{', '.join(ai_signals[:3]) if ai_signals else 'No additional signals'}

⏰ {signal['time'].strftime('%Y-%m-%d %H:%M UTC')}
"""
        
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        print(f"Signal sent: {asset_name} {signal['direction']}")
    except Exception as e:
        print(f"Telegram error: {e}")

async def send_telegram_auto_trade(signal, trade_result, action="OPEN"):
    """Send Telegram notification for auto-executed trades"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        asset_name = ASSET_NAMES.get(signal['symbol'], signal['symbol'])
        
        if action == "OPEN":
            direction_emoji = "🟢" if signal['direction'] == "LONG" else "🔴"
            leverage = trade_result.get('leverage', LEVERAGE)
            leverage_tier = trade_result.get('leverage_tier', 'FIXED')
            tier_emoji = {"STRONG": "💪", "MEDIUM": "✊", "WEAK": "👌", "MINIMAL": "☝️", "FIXED": "🔒"}.get(leverage_tier, "📊")
            message = f"""
🤖 <b>AUTO-TRADE EXECUTED</b> 🤖

{direction_emoji} <b>{asset_name} {signal['direction']}</b>
{tier_emoji} <b>Leverage:</b> {leverage}x ({leverage_tier})
<b>Size:</b> ${trade_result.get('size', 0) * trade_result['price']:.2f}

💰 <b>Pozicija atidaryta:</b>
<b>Entry:</b> ${trade_result['price']:.2f}
<b>Stop Loss:</b> ${trade_result['sl']:.2f}
<b>TP1:</b> ${trade_result['tp1']:.2f}
<b>TP2:</b> ${trade_result['tp2']:.2f}
<b>TP3:</b> ${trade_result['tp3']:.2f}

📊 <b>Signal Score:</b> {signal.get('score', 0)}/100
🔢 <b>Confirmations:</b> {signal.get('confirmation_count', 0)}

📈 <b>Dienos statistika:</b>
Trades: {auto_trading_state['daily_trades']}
P&L: ${auto_trading_state['daily_pnl']:.2f}
W/L: {auto_trading_state['daily_wins']}/{auto_trading_state['daily_losses']}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
        elif action == "CLOSE":
            pnl = trade_result.get('pnl', 0)
            pnl_emoji = "🟢" if pnl > 0 else "🔴"
            reason = trade_result.get('reason', 'MANUAL')
            message = f"""
{pnl_emoji} <b>POZICIJA UŽDARYTA</b> {pnl_emoji}

<b>Asset:</b> {asset_name} {trade_result.get('direction', '')}
<b>Entry:</b> ${trade_result.get('entry_price', 0):.2f}
<b>Close:</b> ${trade_result.get('close_price', 0):.2f}

💰 <b>P&L:</b> ${pnl:.2f}
<b>Priežastis:</b> {reason}

📈 <b>Dienos statistika:</b>
Trades: {auto_trading_state['daily_trades']}
P&L: ${auto_trading_state['daily_pnl']:.2f}
W/L: {auto_trading_state['daily_wins']}/{auto_trading_state['daily_losses']}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
        
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        print(f"  📱 Auto-trade notification sent: {asset_name} {action}")
    except Exception as e:
        print(f"  ⚠️ Telegram auto-trade error: {e}")

async def send_position_update(position, update_type, current_price, extra_info=None, extra_data=None):
    """Send Telegram notification for position updates (trailing, breakeven, TP/SL hits)"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        asset_name = ASSET_NAMES.get(position['symbol'], position['symbol'])
        direction = position['direction']
        entry = position['entry_price']
        
        # v8.9.6: Calculate profit using actual position size, not fixed leverage
        position_size = position.get('remaining_size', position.get('size', 0))
        
        if direction == "LONG":
            profit_pct = ((current_price - entry) / entry) * 100
            profit_usd = (current_price - entry) * position_size
        else:
            profit_pct = ((entry - current_price) / entry) * 100
            profit_usd = (entry - current_price) * position_size
        
        profit_emoji = "+" if profit_pct >= 0 else ""
        
        if update_type == "TRAILING_UPDATE":
            new_sl = extra_info
            # Calculate locked profit based on new SL (guaranteed minimum profit)
            if direction == "LONG":
                locked_profit_usd = (new_sl - entry) * position_size
            else:
                locked_profit_usd = (entry - new_sl) * position_size
            locked_emoji = "+" if locked_profit_usd >= 0 else ""
            
            message = f"""
📈 <b>TRAILING STOP UPDATE</b> 📈

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>Current:</b> ${current_price:.2f} ({profit_emoji}{profit_pct:.1f}%)

🔄 <b>Stop Loss atnaujintas:</b>
<b>Senas SL:</b> ${position['original_sl']:.2f}
<b>Naujas SL:</b> ${new_sl:.2f}

💰 <b>Užfiksuotas pelnas:</b> {locked_emoji}${abs(locked_profit_usd):.2f}

⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "BREAKEVEN":
            message = f"""
🛡️ <b>BREAKEVEN AKTYVUOTAS</b> 🛡️

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>Current:</b> ${current_price:.2f} ({profit_emoji}{profit_pct:.1f}%)

✅ <b>TP1 pasiektas!</b>
<b>Stop Loss perkeltas į Entry:</b> ${entry:.2f}

📊 Pozicija dabar be rizikos!
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "TP1_HIT":
            partial_msg = "🤖 Auto-close 33% aktyvuotas..." if PARTIAL_TP_ENABLED else f"📌 Rekomenduojama: Uždaryti {PARTIAL_TP1_PCT*100:.0f}% pozicijos"
            message = f"""
🎯 <b>TP1 PASIEKTAS!</b> 🎯

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>TP1:</b> ${position['tp1']:.2f}

💰 <b>Pelnas:</b> {profit_emoji}{profit_pct:.1f}%

{partial_msg}
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "TP2_HIT":
            partial_msg = "🤖 Auto-close 33% aktyvuotas..." if PARTIAL_TP_ENABLED else "📌 Rekomenduojama: Uždaryti dar 33% pozicijos"
            message = f"""
🎯🎯 <b>TP2 PASIEKTAS!</b> 🎯🎯

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>TP2:</b> ${position['tp2']:.2f}

💰 <b>Pelnas:</b> {profit_emoji}{profit_pct:.1f}%

{partial_msg}
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "PARTIAL_CLOSE":
            closed_pct = extra_data.get('closed_pct', 0.33) * 100
            reason = extra_data.get('reason', 'TP')
            closed_size = extra_data.get('closed_size', 0)
            remaining = position.get('remaining_size', 0)
            message = f"""
✅ <b>PARTIAL CLOSE EXECUTED!</b> ✅

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>Exit Price:</b> ${current_price:.2f}

🤖 <b>Auto-close at {reason}:</b>
<b>Uždaryta:</b> {closed_pct:.0f}% ({closed_size:.6f})
<b>Likusi pozicija:</b> {remaining:.6f}

💰 <b>Užfiksuotas pelnas:</b> {profit_emoji}{profit_pct:.1f}%

{'🎯 Laukiame TP2/TP3...' if reason == 'TP1' else '🎯 Laukiame TP3 arba SL...'}
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "TP3_HIT":
            message = f"""
🏆 <b>TP3 PASIEKTAS - FULL WIN!</b> 🏆

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>TP3:</b> ${position['tp3']:.2f}

💰 <b>Galutinis pelnas:</b> {profit_emoji}{profit_pct:.1f}%

✅ Pozicija uždaryta su maksimaliu pelnu!
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        elif update_type == "SL_HIT":
            sl_type = "Trailing SL" if position['trailing_active'] else ("Breakeven" if position['breakeven_active'] else "Stop Loss")
            message = f"""
🛑 <b>{sl_type.upper()} SUVEIKĖ</b> 🛑

<b>Asset:</b> {asset_name} {direction}
<b>Entry:</b> ${entry:.2f}
<b>Exit:</b> ${current_price:.2f}

{'💰' if profit_pct >= 0 else '📉'} <b>Rezultatas:</b> {profit_emoji}{profit_pct:.1f}%

{'✅ Pelnas užfiksuotas trailing stop!' if profit_pct >= 0 else '❌ Nuostolis pagal planą.'}
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"""
        
        else:
            return
        
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        print(f"Position update sent: {asset_name} {update_type}")
    except Exception as e:
        print(f"Telegram position update error: {e}")

async def manage_open_positions():
    """Check and manage all open positions for trailing stops and TP/SL hits"""
    global open_positions
    
    positions_to_close = []
    
    # v8.9.6: Iterate over copy to avoid "dictionary changed size during iteration" error
    for symbol, position in list(open_positions.items()):
        try:
            df = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, 10)
            if df is None:
                continue
            
            current_price = df['close'].iloc[-1]
            updates = check_position_status(position, current_price)
            
            asset_name = ASSET_NAMES.get(symbol, symbol)
            
            # v8.9.24: Deduplication - prevent duplicate notifications
            # Send notifications for updates (with deduplication flags)
            if updates['breakeven_update'] and not position.get('breakeven_notified'):
                await send_position_update(position, "BREAKEVEN", current_price)
                position['breakeven_notified'] = True
            
            if updates['tp1_hit'] and not position.get('tp1_notified'):
                await send_position_update(position, "TP1_HIT", current_price)
                position['tp1_notified'] = True
                
                if PARTIAL_TP_ENABLED and not position.get('tp1_partial_closed'):
                    # v8.9.24: NET PROFIT ENGINE - check if TP1 makes sense after fees
                    entry_price = position['entry_price']
                    original_sl = position['original_sl']
                    pos_size = position.get('remaining_size', position.get('size', 0))
                    position_size_usd = pos_size * current_price
                    
                    # Calculate risk in USD (SL distance)
                    if position['direction'] == "LONG":
                        sl_distance_pct = abs(entry_price - original_sl) / entry_price
                    else:
                        sl_distance_pct = abs(original_sl - entry_price) / entry_price
                    risk_usd = pos_size * entry_price * sl_distance_pct
                    
                    # TP1 is typically 1R
                    rr_target = 1.0
                    
                    profit_decision = net_profit_engine(
                        position_size_usd=position_size_usd,
                        rr_target=rr_target,
                        risk_usd=risk_usd
                    )
                    
                    if not profit_decision.allow_tp:
                        print(f"  🚫 TP1 BLOCKED: Net ${profit_decision.estimated_net_profit_usd} | "
                              f"Fees ${profit_decision.fees_usd} | Need RR ≥ {profit_decision.min_rr_required}")
                        # Skip partial close - let trade run to TP2/TP3
                        position['tp1_partial_closed'] = True  # Mark as handled
                        position['tp1_skipped_low_profit'] = True
                    else:
                        print(f"  ✅ TP1 ALLOWED: Net ${profit_decision.estimated_net_profit_usd} | Fees ${profit_decision.fees_usd}")
                        result = await close_partial_position(
                            symbol=symbol,
                            direction=position['direction'],
                            close_pct=PARTIAL_TP1_PCT,
                            current_price=current_price,
                            reason="TP1",
                            original_size=position.get('original_size')
                        )
                        if result['success']:
                            position['tp1_partial_closed'] = True
                            position['remaining_size'] = result['remaining_size']
                            if not position.get('tp1_partial_notified'):
                                await send_position_update(position, "PARTIAL_CLOSE", current_price, 
                                                           extra_data={'closed_pct': PARTIAL_TP1_PCT, 'reason': 'TP1', 
                                                                      'closed_size': result['closed_size']})
                                position['tp1_partial_notified'] = True
                        else:
                            print(f"  ⚠️ Partial close failed at TP1: {result.get('reason')}")
            
            if updates['tp2_hit'] and not position.get('tp2_notified'):
                await send_position_update(position, "TP2_HIT", current_price)
                position['tp2_notified'] = True
                
                if PARTIAL_TP_ENABLED and not position.get('tp2_partial_closed'):
                    # v8.9.24: NET PROFIT ENGINE - check if TP2 makes sense after fees
                    entry_price = position['entry_price']
                    original_sl = position['original_sl']
                    pos_size = position.get('remaining_size', position.get('size', 0))
                    position_size_usd = pos_size * current_price
                    
                    # Calculate risk in USD (SL distance)
                    if position['direction'] == "LONG":
                        sl_distance_pct = abs(entry_price - original_sl) / entry_price
                    else:
                        sl_distance_pct = abs(original_sl - entry_price) / entry_price
                    risk_usd = pos_size * entry_price * sl_distance_pct
                    
                    # TP2 is typically 2R
                    rr_target = 2.0
                    
                    profit_decision = net_profit_engine(
                        position_size_usd=position_size_usd,
                        rr_target=rr_target,
                        risk_usd=risk_usd
                    )
                    
                    if not profit_decision.allow_tp:
                        print(f"  🚫 TP2 BLOCKED: Net ${profit_decision.estimated_net_profit_usd} | "
                              f"Fees ${profit_decision.fees_usd} | Need RR ≥ {profit_decision.min_rr_required}")
                        # Skip partial close - let trade run to TP3
                        position['tp2_partial_closed'] = True  # Mark as handled
                        position['tp2_skipped_low_profit'] = True
                    else:
                        print(f"  ✅ TP2 ALLOWED: Net ${profit_decision.estimated_net_profit_usd} | Fees ${profit_decision.fees_usd}")
                        result = await close_partial_position(
                            symbol=symbol,
                            direction=position['direction'],
                            close_pct=PARTIAL_TP2_PCT,
                            current_price=current_price,
                            reason="TP2",
                            original_size=position.get('original_size')
                        )
                        if result['success']:
                            position['tp2_partial_closed'] = True
                            position['remaining_size'] = result['remaining_size']
                            if not position.get('tp2_partial_notified'):
                                await send_position_update(position, "PARTIAL_CLOSE", current_price,
                                                           extra_data={'closed_pct': PARTIAL_TP2_PCT, 'reason': 'TP2',
                                                                      'closed_size': result['closed_size']})
                                position['tp2_partial_notified'] = True
                        else:
                            print(f"  ⚠️ Partial close failed at TP2: {result.get('reason')}")
            
            if updates['trailing_update']:
                # v8.9.24: Only notify on significant trailing updates (every 1.0% instead of 0.5%)
                old_sl = position.get('last_notified_sl', position['original_sl'])
                sl_change_pct = abs((updates['trailing_update'] - old_sl) / old_sl * 100)
                # Also add minimum time between trailing notifications (60 seconds)
                last_trailing_notify = position.get('last_trailing_notify_time')
                time_ok = last_trailing_notify is None or (datetime.now(timezone.utc) - last_trailing_notify).total_seconds() >= 60
                if sl_change_pct >= 1.0 and time_ok:
                    # v8.9.21: Update exchange-side SL when trailing moves significantly
                    remaining_size = position.get('remaining_size', position.get('size', 0))
                    if position.get('exchange_sl_order_id') and remaining_size > 0:
                        await update_exchange_sl(symbol, updates['trailing_update'], remaining_size)
                    
                    await send_position_update(position, "TRAILING_UPDATE", current_price, updates['trailing_update'])
                    position['last_notified_sl'] = updates['trailing_update']
                    position['last_trailing_notify_time'] = datetime.now(timezone.utc)
            
            if updates['closed']:
                # v8.6: Auto-close position on Kraken when SL/TP3 is hit
                # v8.9.6: Close ALL positions (bot-opened AND Kraken-imported)
                # v8.9.6: Send notification ONLY AFTER successful close
                close_success = False
                if AUTO_TRADING_ENABLED:
                    should_close = False
                    if updates['close_reason'] == "SL_HIT" and AUTO_CLOSE_ON_SL:
                        should_close = True
                    elif updates['close_reason'] == "TP3_HIT" and AUTO_CLOSE_ON_TP3:
                        should_close = True
                    
                    if should_close:
                        print(f"  🔄 Closing {asset_name} position on Kraken ({updates['close_reason']})...")
                        close_result = await close_full_position(symbol, updates['close_reason'])
                        if close_result['success']:
                            close_success = True
                            # Send Telegram notification AFTER successful close
                            if updates['close_reason'] == "TP3_HIT":
                                await send_position_update(position, "TP3_HIT", current_price)
                            elif updates['close_reason'] == "SL_HIT":
                                await send_position_update(position, "SL_HIT", current_price)
                            
                            await send_telegram_auto_trade(
                                {'symbol': symbol, 'direction': position['direction']},
                                close_result,
                                "CLOSE"
                            )
                            print(f"  ✅ {asset_name}: Position closed on Kraken - {updates['close_reason']}")
                        else:
                            print(f"  ❌ {asset_name}: Failed to close on Kraken - {close_result.get('reason', 'Unknown error')}")
                            # Don't remove from tracking if close failed
                            continue
                    else:
                        # Auto-close disabled, just notify
                        if updates['close_reason'] == "TP3_HIT":
                            await send_position_update(position, "TP3_HIT", current_price)
                        elif updates['close_reason'] == "SL_HIT":
                            await send_position_update(position, "SL_HIT", current_price)
                        close_success = True
                        print(f"  {asset_name}: Position closed - {updates['close_reason']}")
                else:
                    # Auto-trading disabled, just notify
                    if updates['close_reason'] == "TP3_HIT":
                        await send_position_update(position, "TP3_HIT", current_price)
                    elif updates['close_reason'] == "SL_HIT":
                        await send_position_update(position, "SL_HIT", current_price)
                    close_success = True
                    print(f"  {asset_name}: Position closed - {updates['close_reason']}")
                
                # Only remove from tracking and mark result if close was successful
                if close_success:
                    auto_mark_signal_result(position, updates['close_reason'], current_price)
                    positions_to_close.append(symbol)
            else:
                # Status update
                trailing_status = "TRAILING" if position['trailing_active'] else ("BE" if position['breakeven_active'] else "")
                tp_status = f"TP1{'✓' if position['tp1_hit'] else ''} TP2{'✓' if position['tp2_hit'] else ''}"
                if trailing_status or position['tp1_hit']:
                    print(f"  {asset_name}: ${current_price:.2f} | SL: ${position['current_sl']:.2f} | {trailing_status} {tp_status}")
        
        except Exception as e:
            print(f"Error managing position {symbol}: {e}")
    
    # Remove closed positions
    for symbol in positions_to_close:
        del open_positions[symbol]

# ================================
# MAIN SIGNAL LOOP
# ================================
async def check_signals():
    global last_signals, signals_history, bot_stats
    
    print(f"\n{'='*50}")
    print(f"Checking signals at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"{'='*50}")
    
    # v8.9.13: Amateur Hour warning
    try:
        amateur_check = AmateurHourFilter.is_amateur_hour(check_crypto=True)
        if amateur_check.get('is_amateur_hour'):
            remaining = amateur_check.get('minutes_remaining', 0)
            print(f"⏰ Amateur Hour - pirmos dienos minutės, liko {remaining} min. Daugiau triukšmo.")
    except Exception:
        pass
    
    # Fetch open positions from Kraken
    if POSITION_TRACKING_ENABLED:
        await fetch_kraken_positions()
    
    # v8.9.19: Collect all valid signals first, then sort by score before auto-trading
    collected_signals = []  # List of (symbol, signal, asset_name) tuples
    
    for symbol in FUTURES_ASSETS:
        try:
            # 4H Macro analysis (big picture)
            df_4h = await fetch_ohlcv(symbol, TIMEFRAME_MACRO, 100)
            macro = analyze_macro(df_4h)
            
            # 1H Trend analysis
            df_1h = await fetch_ohlcv(symbol, TIMEFRAME_TREND, 100)
            trend, trend_score = analyze_trend(df_1h)
            
            # 15m Entry signals
            df_15m = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, 100)
            
            # v8.9.3: Get quant bias for this asset
            asset_quant_bias = 0
            asset_name = symbol.replace('PF_', '').replace('USD', '')
            if asset_name in quant_results and quant_results[asset_name]:
                asset_quant_bias, _ = quant_engine.get_quant_signal_bias(quant_results[asset_name])
            
            signal = generate_entry_signal(symbol, df_15m, trend, df_htf=df_1h, macro_data=macro, df_1h=df_1h, quant_bias=asset_quant_bias)
            
            # ================================
            # v8.9.24: 5m ENTRY OPTIMIZER (FUND MODE)
            # ================================
            # 5m NO LONGER generates signals - only optimizes entry for valid 15m signals
            # Fetched later when signal is confirmed, before auto-trading
            df_5m = None  # Will be fetched only if needed for entry optimization
            df_daily = await fetch_ohlcv(symbol, TIMEFRAME_DAILY, 30)
            # ================================
            
            # ================================
            # v8.6 BOX STRATEGY (The Rumers)
            # ================================
            # Check for Box breakout on 1H timeframe
            box_signal = None
            if df_1h is not None and len(df_1h) >= 25:
                box_result = BoxStrategy.check_box_strategy(df_1h, lookback=20, threshold_pct=2.5)
                
                # Log when box found but volume not confirmed
                if box_result['box_found'] and not box_result['breakout_confirmed']:
                    asset_name = ASSET_NAMES.get(symbol, symbol)
                    print(f"  📦 {asset_name}: Box found but no volume-confirmed breakout")
                
                if box_result['breakout_confirmed'] and box_result['signal']:
                    # v8.8: 1H RSI PRE-FILTER for Box Breakout
                    rsi_filter_ok, rsi_filter_reason = check_1h_rsi_prefilter(df_1h, box_result['signal'], lookback=6)
                    if not rsi_filter_ok:
                        print(f"  ❌ {symbol}: BOX {box_result['signal']} blocked - {rsi_filter_reason}")
                    else:
                        atr_1h = calc_atr(df_1h).iloc[-1]
                        current_price = df_1h['close'].iloc[-1]
                        
                        # v8.7: TP1 pagal S/R zonas, TP2/TP3 pagal box range
                        box_range = box_result['box_high'] - box_result['box_low']
                        tp2_dist = box_range * 2.0  # 2x box range
                        tp3_dist = box_range * 3.0  # 3x box range
                        
                        box_signals = ['BOX_BREAKOUT'] + box_result['notes']
                        # v8.8: Add 1H RSI filter confirmation
                        if "1H_RSI" in rsi_filter_reason:
                            box_signals.append(rsi_filter_reason.split(" ")[0])
                        
                        if box_result['signal'] == 'LONG':
                            tp1_sr, tp1_used_sr = get_tp1_from_sr_zone(current_price, "LONG", df_1h, atr_1h)
                            if tp1_used_sr:
                                box_signals.append("TP1_SR_ZONE")
                            box_signal = {
                                "symbol": symbol,
                                "direction": "LONG",
                                "score": 70,
                                "base_score": 60,
                                "signals": box_signals,
                                "price": current_price,
                                "sl": box_result['stop_loss'],
                                "tp1": tp1_sr,
                                "tp2": current_price + tp2_dist,
                                "tp3": current_price + tp3_dist,
                                "atr": atr_1h,
                                "rsi": calc_rsi(df_1h['close']).iloc[-1],
                                "trend": trend,
                                "confidence": 0.65,
                                "modules_used": 1,
                                "entry_type": "MARKET",
                                "entry_reason": "📦 Box Breakout UP",
                                "time": datetime.now(timezone.utc)
                            }
                        else:  # SHORT
                            tp1_sr, tp1_used_sr = get_tp1_from_sr_zone(current_price, "SHORT", df_1h, atr_1h)
                            if tp1_used_sr:
                                box_signals.append("TP1_SR_ZONE")
                            box_signal = {
                                "symbol": symbol,
                                "direction": "SHORT",
                                "score": 70,
                                "base_score": 60,
                                "signals": box_signals,
                                "price": current_price,
                                "sl": box_result['stop_loss'],
                                "tp1": tp1_sr,
                                "tp2": current_price - tp2_dist,
                                "tp3": current_price - tp3_dist,
                                "atr": atr_1h,
                                "rsi": calc_rsi(df_1h['close']).iloc[-1],
                                "trend": trend,
                                "confidence": 0.65,
                                "modules_used": 1,
                                "entry_type": "MARKET",
                                "entry_reason": "📦 Box Breakout DOWN",
                                "time": datetime.now(timezone.utc)
                            }
            
            # v8.9.24: Box signal overrides regular signal (5m no longer generates signals)
            if box_signal:
                print(f"  📦 BOX BREAKOUT detected: {box_signal['direction']}")
                signal = box_signal
            # ================================
            
            asset_name = ASSET_NAMES.get(symbol, symbol)
            current_price = df_15m['close'].iloc[-1] if df_15m is not None else 0
            macro_info = f" | 4H: {macro['bias']}" if macro['signals'] else ""
            squeeze_info = " 🔥" if macro.get('squeeze') else ""
            
            # v8.9.8: Market Structure analysis
            structure_result = analyze_market_structure(df_1h, lookback=50)
            structure_info = ""
            if structure_result['structure'] == 'BULLISH':
                structure_info = f" | 📈 {structure_result['structure']}"
            elif structure_result['structure'] == 'BEARISH':
                structure_info = f" | 📉 {structure_result['structure']}"
            if structure_result['structure_break']:
                structure_info += f" ⚡{structure_result['structure_break']}"
            # v8.9.12: CHoCH detection
            if structure_result.get('choch'):
                choch = structure_result['choch']
                choch_level = structure_result.get('choch_level', 0)
                if choch == 'BULLISH_CHOCH':
                    structure_info += f" | 🔀 CHoCH↑ ${choch_level:.0f}"
                elif choch == 'BEARISH_CHOCH':
                    structure_info += f" | 🔀 CHoCH↓ ${choch_level:.0f}"
            
            # v8.9.9: VWAP analysis
            vwap_signal = get_vwap_signal(df_1h, period=VWAP_PERIOD)
            vwap_info = ""
            if vwap_signal.get('valid', False):
                vwap_bias = vwap_signal.get('bias', 'NEUTRAL')
                price_vs = vwap_signal.get('price_vs_vwap', 'UNKNOWN')
                if price_vs == 'ABOVE':
                    vwap_info = f" | 🔷 VWAP↑"
                elif price_vs == 'BELOW':
                    vwap_info = f" | 🔶 VWAP↓"
            
            # v8.9.10: S/R Flip detection
            sr_flip_result = detect_sr_flip(df_1h, lookback=100)
            sr_flip_info = ""
            if sr_flip_result.get('has_flip_nearby', False):
                nearest = sr_flip_result.get('nearest_flip', {})
                flip_type = nearest.get('type', '')
                flip_price = nearest.get('price', 0)
                if flip_type == 'R_TO_S':
                    sr_flip_info = f" | 🔄 R→S ${flip_price:.0f}"
                elif flip_type == 'S_TO_R':
                    sr_flip_info = f" | 🔄 S→R ${flip_price:.0f}"
            
            # v8.9.12: Breaker Block detection
            breaker_info = ""
            try:
                breakers = BreakerBlock.find_breaker_blocks(df_1h, lookback=100)
                breaker_proximity = BreakerBlock.check_breaker_proximity(df_1h, breakers, tolerance_pct=0.5)
                if breaker_proximity.get('near_bullish_breaker'):
                    level = breaker_proximity.get('bullish_breaker_level', 0)
                    breaker_info = f" | 💚 BreakerS ${level:.0f}"
                elif breaker_proximity.get('near_bearish_breaker'):
                    level = breaker_proximity.get('bearish_breaker_level', 0)
                    breaker_info = f" | ❤️ BreakerR ${level:.0f}"
            except Exception:
                pass
            
            # v8.9.13: Elliott Wave Phase detection
            elliott_info = ""
            try:
                elliott_phase = ElliottWavePhase.detect_phase(df_1h, lookback=50)
                if elliott_phase['phase'] != 'UNKNOWN' and elliott_phase['confidence'] >= 50:
                    phase = elliott_phase['phase']
                    direction = elliott_phase['trend_direction']
                    wave_count = elliott_phase['wave_count']
                    if phase == 'IMPULSE':
                        elliott_info = f" | 🌊 {wave_count}W-{direction[0]}"
                    else:
                        elliott_info = f" | 🌊 ABC"
            except Exception:
                pass
            
            # v8.9.13: 0.718 Fibonacci Sweet Spot
            fib718_info = ""
            try:
                fib718 = FibonacciSweetSpot.find_sweet_spot_zones(df_1h, lookback=50)
                if fib718.get('near_bullish_zone'):
                    fib718_info = f" | 🎯 Fib.718S"
                elif fib718.get('near_bearish_zone'):
                    fib718_info = f" | 🎯 Fib.718R"
            except Exception:
                pass
            
            # v8.9.13: Exhaustion Gap detection
            exhaust_info = ""
            try:
                exhaust_gap = ExhaustionGap.detect_exhaustion_gap(df_1h, lookback=20)
                if exhaust_gap.get('exhaustion_gap_up'):
                    exhaust_info = f" | ⚠️ ExhGap↑"
                elif exhaust_gap.get('exhaustion_gap_down'):
                    exhaust_info = f" | ⚠️ ExhGap↓"
            except Exception:
                pass
            
            # v8.9.13: Candle Strength Analysis
            candle_strength_info = ""
            try:
                candle_strength = CandleStrengthAnalyzer.analyze_candle_strength(df_1h)
                if candle_strength.get('is_decision_candle'):
                    candle_strength_info = f" | 💪 STRONG"
                elif candle_strength.get('strength') == 'INDECISION':
                    candle_strength_info = f" | 🤔 DOJI"
            except Exception:
                pass
            
            # v8.9.14: Money Flow Index (MFI)
            mfi_info = ""
            try:
                mfi_data = MoneyFlowIndex.calculate_mfi(df_1h)
                mfi_val = mfi_data.get('mfi', 50)
                if mfi_data.get('overbought'):
                    mfi_info = f" | 💰 MFI:{mfi_val:.0f}⬆️"
                elif mfi_data.get('oversold'):
                    mfi_info = f" | 💰 MFI:{mfi_val:.0f}⬇️"
            except Exception:
                pass
            
            # v8.9.14: Chaikin Money Flow (CMF)
            cmf_info = ""
            try:
                cmf_data = ChaikinMoneyFlow.calculate_cmf(df_1h)
                cmf_val = cmf_data.get('cmf', 0)
                if cmf_data.get('accumulation') and cmf_data.get('strength') in ['STRONG', 'MODERATE']:
                    cmf_info = f" | 📈 CMF+{cmf_val:.2f}"
                elif cmf_data.get('distribution') and cmf_data.get('strength') in ['STRONG', 'MODERATE']:
                    cmf_info = f" | 📉 CMF{cmf_val:.2f}"
            except Exception:
                pass
            
            # v8.9.14: Money Flow Divergence
            mf_div_info = ""
            try:
                mf_div = MoneyFlowDivergence.detect_mfi_divergence(df_1h)
                if mf_div.get('divergence') == 'BULLISH':
                    mf_div_info = f" | 🔀 MFI_DIV↑"
                elif mf_div.get('divergence') == 'BEARISH':
                    mf_div_info = f" | 🔀 MFI_DIV↓"
            except Exception:
                pass
            
            # v8.9.14: Wave Score
            wave_info = ""
            try:
                wave_data = WaveScore.calculate_wave_score(df_1h)
                if wave_data.get('momentum_reversal'):
                    direction = wave_data.get('momentum_direction', '')
                    if direction == 'UP':
                        wave_info = f" | 🌊 WAVE↑"
                    elif direction == 'DOWN':
                        wave_info = f" | 🌊 WAVE↓"
            except Exception:
                pass
            
            # v8.9.14: Stop Hunt Detection
            stop_hunt_info = ""
            try:
                stop_hunt = StopHuntDetector.detect_stop_hunt(df_1h)
                if stop_hunt.get('stop_hunt'):
                    direction = stop_hunt.get('direction', '')
                    if direction == 'UP':
                        stop_hunt_info = f" | 🎯 HUNT↑"
                    elif direction == 'DOWN':
                        stop_hunt_info = f" | 🎯 HUNT↓"
            except Exception:
                pass
            
            # Get current RSI for logging
            rsi_15m = calc_rsi(df_15m['close']).iloc[-1] if df_15m is not None and len(df_15m) >= 14 else 50
            rsi_info = ""
            if rsi_15m <= RSI_OVERSOLD:
                rsi_info = f" | 🟢 RSI: {rsi_15m:.1f} (OVERSOLD)"
            elif rsi_15m >= RSI_OVERBOUGHT:
                rsi_info = f" | 🔴 RSI: {rsi_15m:.1f} (OVERBOUGHT)"
            elif rsi_15m < 35 or rsi_15m > 65:
                rsi_info = f" | RSI: {rsi_15m:.1f}"
            
            print(f"{asset_name}: ${current_price:.2f} | Trend: {trend} | Score: {trend_score}{macro_info}{squeeze_info}{structure_info}{vwap_info}{sr_flip_info}{breaker_info}{elliott_info}{fib718_info}{exhaust_info}{candle_strength_info}{mfi_info}{cmf_info}{mf_div_info}{wave_info}{stop_hunt_info}{rsi_info}")
            
            if signal:
                # Check if position already open on Kraken
                if POSITION_TRACKING_ENABLED and has_open_position(symbol, signal['direction']):
                    print(f"  → Skipped: {signal['direction']} position already open on Kraken")
                    continue
                
                # Check all filters before sending signal
                is_blocked, block_reasons = check_all_filters(signal['direction'], signal)
                
                if is_blocked:
                    print(f"  → Signal BLOCKED: {', '.join(block_reasons)}")
                    continue
                
                # v8.9.23: RR ENGINE - Apply penalty/bonus from soft penalty system
                rr_ratio = signal.get('rr_ratio', 0)
                rr_penalty = signal.get('rr_penalty', 0)
                rr_min = signal.get('rr_min', 1.5)
                scalp_mode = signal.get('scalp_mode', False)
                
                if rr_penalty < 0:
                    # Apply penalty
                    signal['score'] = max(0, signal['score'] + rr_penalty)
                    signal['signals'].append(f"RR_PENALTY_{abs(rr_penalty):.0f}")
                elif rr_ratio >= EXCELLENT_RR_RATIO:
                    signal['score'] = min(100, signal['score'] + 10)
                    signal['signals'].append(f"RR_EXCELLENT_{rr_ratio:.1f}")
                elif rr_ratio >= GOOD_RR_RATIO:
                    signal['score'] = min(100, signal['score'] + 5)
                    signal['signals'].append(f"RR_GOOD_{rr_ratio:.1f}")
                
                if scalp_mode:
                    signal['signals'].append("SCALP_MODE")
                
                # v8.9.23: SIGNAL DENSITY ENGINE - Soft filters & adaptive thresholds
                trade_mode = signal.get('trade_mode', 'NORMAL')
                trend_strength = "STRONG" if trend in ["STRONG_BULL", "STRONG_BEAR"] else ("NORMAL" if trend in ["BULL", "BEAR"] else "WEAK")
                volatility_spike = signal.get('atr_ratio', 1.0) >= 1.3
                is_consolidating_flag = signal.get('consolidation', False)
                
                # Session liquidity (London/NY hours = liquid)
                current_hour = datetime.now(timezone.utc).hour
                session_liquid = (7 <= current_hour <= 16) or (13 <= current_hour <= 21)  # London or NY
                
                density_ctx = DensityContext(
                    base_score=signal.get('score', 0),
                    trend_strength=trend_strength,
                    market_regime=market_regime_state.get('regime', 'NEUTRAL'),
                    volatility_spike=volatility_spike,
                    consolidation=is_consolidating_flag,
                    session_liquid=session_liquid,
                    trade_mode=trade_mode
                )
                
                density_result = evaluate_signal_density(density_ctx)
                
                if not density_result.allowed:
                    print(f"  → DENSITY BLOCKED: {density_result.reason} (score {density_result.final_score:.1f} < {density_result.min_score_required:.1f})")
                    continue
                
                # Update score with density adjustments
                signal['score'] = density_result.final_score
                signal['density_min_score'] = density_result.min_score_required
                
                last_sig = last_signals.get(symbol, {})
                last_signal_time = last_sig.get('time')
                last_direction = last_sig.get('direction')
                last_price = last_sig.get('price', 0)
                cooldown = timedelta(hours=1)
                
                # Check for duplicate: same direction and similar price within cooldown
                is_duplicate = False
                if last_signal_time and (signal['time'] - last_signal_time) <= cooldown:
                    if last_direction == signal['direction']:
                        price_diff = abs(signal['price'] - last_price) / last_price if last_price else 0
                        if price_diff < 0.02:  # Within 2% = duplicate
                            is_duplicate = True
                            print(f"  → Duplicate signal skipped (same direction, {price_diff*100:.1f}% price diff)")
                
                if is_duplicate:
                    pass  # Skip duplicate
                elif last_signal_time is None or (signal['time'] - last_signal_time) > cooldown:
                    # v8.9.19: Collect signal for sorted processing instead of immediate execution
                    collected_signals.append({
                        'symbol': symbol,
                        'signal': signal,
                        'asset_name': asset_name,
                        'score': signal.get('score', 0),
                        'confluence_score': signal.get('confluence_score', 0)
                    })
                    print(f"Signal sent: {asset_name} {signal['direction']}")
                else:
                    print(f"  → Signal on cooldown (1h)")
            
            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"Error checking {symbol}: {e}")
    
    # ================================
    # v8.9.19: SORTED SIGNAL PROCESSING
    # Sort by confluence_score (primary) and score (secondary) - highest first
    # ================================
    if collected_signals:
        # Sort: highest confluence_score first, then highest score
        collected_signals.sort(key=lambda x: (x['confluence_score'], x['score']), reverse=True)
        
        # ================================
        # v8.9.21: CONFLICT PREVENTION
        # Only keep ONE signal per symbol (highest scoring direction)
        # Prevents simultaneous LONG/SHORT on same asset
        # ================================
        seen_symbols = {}
        filtered_signals = []
        for sig_data in collected_signals:
            symbol = sig_data['symbol']
            direction = sig_data['signal']['direction']
            
            if symbol not in seen_symbols:
                seen_symbols[symbol] = direction
                filtered_signals.append(sig_data)
            else:
                # Conflict: same symbol, different or same direction already queued
                existing_direction = seen_symbols[symbol]
                if existing_direction != direction:
                    print(f"  ⚠️ CONFLICT BLOCKED: {sig_data['asset_name']} {direction} (already have {existing_direction})")
                else:
                    print(f"  → Duplicate {sig_data['asset_name']} {direction} skipped")
        
        collected_signals = filtered_signals
        
        print(f"\n📊 Processing {len(collected_signals)} signal(s) sorted by score (highest first):")
        for idx, sig_data in enumerate(collected_signals):
            print(f"  {idx+1}. {sig_data['asset_name']}: Score={sig_data['score']}, Confluence={sig_data['confluence_score']}")
        
        # Process signals in sorted order
        for sig_data in collected_signals:
            symbol = sig_data['symbol']
            signal = sig_data['signal']
            asset_name = sig_data['asset_name']
            
            # Send Telegram signal
            await send_telegram_signal(signal)
            
            last_signals[symbol] = signal
            signals_history.append(signal)
            
            # Track signal for WIN/LOSS marking
            signal_id = add_signal_to_tracking(signal)
            print(f"  → Signal tracked: {signal_id}")
            
            # ================================
            # AUTO-TRADING EXECUTION (v8.6 + v8.9 Dynamic Leverage)
            # ================================
            if AUTO_TRADING_ENABLED:
                # v8.9.24: 5m Entry Optimizer - improve entry quality (does NOT block signals)
                entry_optimized = True
                try:
                    df_5m = await fetch_ohlcv(symbol, TIMEFRAME_5M_OPTIMIZE, 20)
                    if df_5m is not None and len(df_5m) >= 5:
                        entry_opt = optimize_entry_5m(
                            df_5m=df_5m,
                            direction=signal['direction'],
                            ideal_entry_price=signal['price'],
                            base_rr=1.5
                        )
                        
                        if not entry_opt.allow_entry:
                            print(f"  ⏳ 5m waiting: {entry_opt.wait_reason}")
                            # Note: We still execute - 5m only suggests, never blocks
                            # In future: could implement retry logic here
                        else:
                            # Apply boosts from good entry
                            if entry_opt.score_boost > 0:
                                signal['score'] = signal.get('score', 0) + entry_opt.score_boost
                                print(f"  ✅ 5m entry boost: +{entry_opt.score_boost} score")
                except Exception as e:
                    print(f"  ⚠️ 5m optimizer error: {e}")
                
                trade_result = await open_position(
                    symbol=symbol,
                    direction=signal['direction'],
                    price=signal['price'],
                    sl=signal['sl'],
                    tp1=signal['tp1'],
                    tp2=signal['tp2'],
                    tp3=signal['tp3'],
                    signal_score=signal.get('score', 0),
                    ml_confidence=signal.get('ml_confidence'),
                    confirmation_count=signal.get('confirmation_count', 0),
                    entry_type=signal.get('entry_type', 'MARKET'),  # v8.9.24: LIMIT/MARKET support
                    scalp_rebound_multiplier=signal.get('scalp_rebound_multiplier', 1.0)
                )
                
                if trade_result['success']:
                    # Send Telegram notification for auto-trade
                    await send_telegram_auto_trade(signal, trade_result, "OPEN")
                else:
                    print(f"  → Auto-trade skipped: {trade_result['reason']}")
            
            bot_stats["total_signals"] += 1
            if signal['direction'] == "LONG":
                bot_stats["long_signals"] += 1
            else:
                bot_stats["short_signals"] += 1
            
            if len(signals_history) > 100:
                signals_history = signals_history[-100:]
    
    bot_stats["last_check"] = datetime.now(timezone.utc)

async def signal_loop():
    print("🚀 Futures Signal Bot v8.9.24 PRO Started!")
    print("📊 v8.9.24: 5m Entry Optimizer (FUND MODE) - 5m tik pagerina entry, neblokuoja signalų!")
    print(f"📊 Assets: {', '.join(ASSET_NAMES.values())}")
    print(f"⏱️ Timeframes: {TIMEFRAME_MACRO} (macro) | {TIMEFRAME_TREND} (trend) | {TIMEFRAME_ENTRY} (entry) | {TIMEFRAME_5M_OPTIMIZE} (optimize)")
    print(f"📱 Telegram: {'Configured' if TELEGRAM_TOKEN else 'NOT CONFIGURED'}")
    print(f"🤖 AUTO-TRADING: {'ON - $' + str(AUTO_TRADE_MARGIN_USD) + ' margin/trade, max ' + str(AUTO_TRADE_MAX_POSITIONS) + ' positions' if AUTO_TRADING_ENABLED else 'OFF'}")
    print(f"💰 Daily Loss Limit: ${DAILY_LOSS_LIMIT_USD}")
    print(f"📈 Trailing Stop: {'ENABLED' if TRAILING_ENABLED else 'DISABLED'} ({TRAILING_DISTANCE_PCT}%)")
    print(f"🛡️ Breakeven at TP1: {'YES' if BREAKEVEN_AT_TP1 else 'NO'}")
    print(f"🤖 Partial TP: {'ON - Auto-close 33% at TP1 & TP2' if PARTIAL_TP_ENABLED else 'OFF'}")
    print(f"🏛️ FOMC Filter: {'ON' if FOMC_BLACKOUT_ENABLED else 'OFF'}")
    print(f"📉 Market Regime: {'ON' if MARKET_REGIME_ENABLED else 'OFF'}")
    print(f"🌐 Macro Filters: SPY={'ON' if SPY_ENABLED else 'OFF'} VIX={'ON' if VIX_ENABLED else 'OFF'} DXY={'ON' if DXY_ENABLED else 'OFF'}")
    print(f"📍 Position Tracking: {'ON - Skips signals for open positions' if POSITION_TRACKING_ENABLED else 'OFF - No API keys'}")
    
    # v8.9.14+: Multi-Collateral balance check
    if POSITION_TRACKING_ENABLED:
        balance = fetch_multi_collateral_balance()
        print(f"💵 Account Balance: ${balance['total_usd']:.2f} (Cash: ${balance['cash_usd']:.2f} | Flex: ${balance['flex_usd']:.2f})")
    
    # Initial macro checks
    print("\n--- Running initial macro checks ---")
    detect_market_regime()
    get_spy_data()
    run_macro_checks()
    
    # v8.9.3: Run quant analysis at startup for counter-trend signals
    global quant_results, quant_correlation, quant_last_update
    print("\n🧮 Running initial quant analysis...")
    try:
        quant_results, quant_correlation = quant_engine.run_all_assets()
        quant_last_update = datetime.now()
        for asset, data in quant_results.items():
            if data:
                bias, signals = quant_engine.get_quant_signal_bias(data)
                print(f"  📊 {asset}: Quant Bias = {bias:+d}")
        print("✅ Quant analysis ready!")
    except Exception as e:
        print(f"⚠️ Quant analysis failed: {e}")
    
    next_fomc = get_next_fomc()
    if next_fomc:
        print(f"📅 Next FOMC: {next_fomc.strftime('%Y-%m-%d %H:%M')} UTC")
    
    print(f"🐂 Market Regime: {market_regime_state['regime']} | LONGs Blocked: {market_regime_state['longs_blocked']}")
    if spy_state['current_price']:
        print(f"📊 SPY: ${spy_state['current_price']:.2f} ({spy_state['daily_change_pct']*100:+.1f}%) | Risk-Off: {spy_state['risk_off']}")
    if macro_state['vix_value']:
        print(f"😱 VIX: {macro_state['vix_value']:.1f} ({macro_state['vix_level']}) | DXY: {macro_state['dxy_trend']}")
    
    # Load existing Kraken positions for trailing stop management
    await load_existing_positions_for_trailing()
    
    # v8.6: Double-check positions before auto-trading starts (prevent duplicate trades)
    if AUTO_TRADING_ENABLED:
        print("\n🔄 Verifying Kraken positions before auto-trading...")
        await asyncio.sleep(5)  # Wait 5 seconds
        kraken_positions['last_fetch'] = None  # Force fresh fetch
        await fetch_kraken_positions()
        print(f"✅ Position verification complete - {len(kraken_positions['positions'])} position(s) detected")
    
    last_macro_check = datetime.now(timezone.utc)
    
    # Send initial heartbeat on startup
    await send_heartbeat()
    
    while True:
        try:
            # v8.6: Reset daily stats at midnight UTC
            reset_daily_stats()
            
            # v8.9.23: Heartbeat only on startup (removed periodic sending)
            # Heartbeat is sent once at bot startup in line 5981
            
            # Run macro checks every hour
            if (datetime.now(timezone.utc) - last_macro_check).total_seconds() >= 3600:
                print("\n--- Hourly macro update ---")
                detect_market_regime()
                get_spy_data()
                run_macro_checks()
                
                print(f"Regime: {market_regime_state['regime']} | SPY: {spy_state['trend']} | VIX: {macro_state['vix_level']}")
                last_macro_check = datetime.now(timezone.utc)
            
            # Check if auto-trading is paused due to daily loss limit
            if AUTO_TRADING_ENABLED and auto_trading_state['is_paused']:
                print(f"⚠️ AUTO-TRADING PAUSED: {auto_trading_state['pause_reason']}")
            
            # Sync positions with Kraken every 5 minutes
            await sync_positions_with_kraken()
            
            # Check FOMC blackout
            is_blackout, fomc_date = is_fomc_blackout()
            if is_blackout:
                print(f"🏛️ FOMC BLACKOUT ACTIVE - Signals paused until {(fomc_date + timedelta(hours=FOMC_BLACKOUT_HOURS_AFTER)).strftime('%H:%M')} UTC")
            
            await check_signals()
            
            # Manage open positions (trailing stops)
            if TRAILING_ENABLED and open_positions:
                print(f"\n--- Managing {len(open_positions)} open position(s) ---")
                await manage_open_positions()
            
            # Increment cycle counters
            bot_stats["cycles_completed"] += 1
            bot_stats["cycles_success"] += 1
            bot_stats["consecutive_errors"] = 0  # Reset error counter on success
            
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            bot_stats["cycles_completed"] += 1
            bot_stats["consecutive_errors"] = bot_stats.get("consecutive_errors", 0) + 1
            error_count = bot_stats["consecutive_errors"]
            
            print(f"⚠️ Loop error #{error_count}: {e}")
            
            # Send alert after 3 consecutive errors
            if error_count == 3:
                try:
                    from telegram import Bot
                    tg_bot = Bot(token=TELEGRAM_TOKEN)
                    await tg_bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"⚠️ BOT ALERT\n\n🔴 3 klaidos iš eilės!\nKlaida: {str(e)[:100]}\n\n🔄 Bandoma atkurti..."
                    )
                except:
                    pass
            
            # Progressive backoff: 30s, 60s, 120s, max 300s
            wait_time = min(30 * (2 ** (error_count - 1)), 300)
            print(f"🔄 Auto-restart po {wait_time}s...")
            await asyncio.sleep(wait_time)
            
            # After 10 consecutive errors, try full reset
            if error_count >= 10:
                print("🔄 FULL RESET - reinitializuojama...")
                try:
                    from telegram import Bot
                    tg_bot = Bot(token=TELEGRAM_TOKEN)
                    await tg_bot.send_message(
                        chat_id=CHAT_ID,
                        text="🔄 BOT FULL RESET\n\n10 klaidų iš eilės - bandoma pilnai atkurti botą..."
                    )
                except:
                    pass
                
                # Reset caches and state
                kraken_positions['last_fetch'] = None
                account_balance_cache['last_update'] = None
                bot_stats["consecutive_errors"] = 0
                
                # Reinitialize
                await fetch_kraken_positions()
                fetch_multi_collateral_balance()
                print("✅ Full reset complete")

# ================================
# FLASK DASHBOARD
# ================================
app = Flask(__name__, static_folder='static', template_folder='templates')

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Futures Signal Bot</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0a0a0f; color: #fff; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { color: #00d4aa; font-size: 2em; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .stat-card { background: #1a1a2e; padding: 20px; border-radius: 10px; text-align: center; }
        .stat-value { font-size: 2em; font-weight: bold; color: #00d4aa; }
        .stat-label { color: #888; margin-top: 5px; }
        .signals { background: #1a1a2e; border-radius: 10px; padding: 20px; }
        .signals h2 { color: #00d4aa; margin-bottom: 15px; }
        .signal { background: #252540; padding: 15px; border-radius: 8px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .signal-long { border-left: 4px solid #00ff88; }
        .signal-short { border-left: 4px solid #ff4466; }
        .signal-info { flex: 1; }
        .signal-asset { font-weight: bold; font-size: 1.2em; }
        .signal-direction { padding: 5px 10px; border-radius: 5px; font-weight: bold; }
        .long { background: #00ff8820; color: #00ff88; }
        .short { background: #ff446620; color: #ff4466; }
        .signal-details { color: #888; font-size: 0.9em; margin-top: 5px; }
        .no-signals { text-align: center; color: #666; padding: 40px; }
        
        .positions-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 30px; }
        @media (max-width: 1200px) { .positions-grid { grid-template-columns: repeat(2, 1fr); } }
        @media (max-width: 600px) { .positions-grid { grid-template-columns: 1fr; } }
        
        .position-card { background: #1a1a2e; border-radius: 12px; padding: 20px; position: relative; }
        .position-active { border: 2px solid #00d4aa; }
        .position-inactive { border: 2px solid #333; opacity: 0.7; }
        .position-long { border-color: #00ff88; }
        .position-short { border-color: #ff4466; }
        
        .position-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .position-asset { font-size: 1.5em; font-weight: bold; color: #fff; }
        .position-direction { padding: 5px 12px; border-radius: 5px; font-weight: bold; font-size: 0.9em; }
        .position-inactive-badge { padding: 5px 12px; border-radius: 5px; background: #333; color: #666; font-size: 0.8em; }
        
        .position-price { font-size: 1.8em; font-weight: bold; color: #fff; margin: 10px 0; }
        .position-pnl { font-size: 1.4em; font-weight: bold; margin-bottom: 15px; }
        .pnl-positive { color: #00ff88; }
        .pnl-negative { color: #ff4466; }
        
        .position-levels { border-top: 1px solid #333; padding-top: 15px; }
        .level-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; }
        .level-label { color: #888; font-size: 0.9em; }
        .level-value { color: #fff; font-weight: 500; }
        .sl-value { color: #ff4466; }
        .tp-hit { color: #00ff88; text-decoration: line-through; }
        
        .trailing-badge { background: #ffd700; color: #000; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; font-weight: bold; margin-left: 5px; }
        .be-badge { background: #00d4aa; color: #000; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; font-weight: bold; margin-left: 5px; }
        .tp-badge { background: #00ff88; color: #000; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; font-weight: bold; margin-left: 5px; }
        
        .position-waiting { color: #666; text-align: center; padding: 30px 0; font-style: italic; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 Futures Signal Bot</h1>
        <p>BTC | ETH | SOL | XRP Perpetuals</p>
        <div style="margin-top: 15px;">
            <a href="/quant" style="display: inline-block; padding: 10px 20px; background: #ffd700; color: #000; text-decoration: none; border-radius: 20px; font-weight: bold; margin: 5px;">🧮 Matematinė Analizė</a>
            <a href="/advanced" style="display: inline-block; padding: 10px 20px; background: #9945FF; color: #fff; text-decoration: none; border-radius: 20px; font-weight: bold; margin: 5px;">🤖 AI + On-Chain</a>
        </div>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_signals }}</div>
            <div class="stat-label">Total Signals</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="color: #00ff88;">{{ stats.long_signals }}</div>
            <div class="stat-label">LONG Signals</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="color: #ff4466;">{{ stats.short_signals }}</div>
            <div class="stat-label">SHORT Signals</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ uptime }}</div>
            <div class="stat-label">Uptime</div>
        </div>
    </div>
    
    <h2 style="color: #00d4aa; margin-bottom: 15px;">📍 Open Positions (Trailing Stop)</h2>
    <div class="positions-grid">
        {% for pos in positions %}
        <div class="position-card {% if pos.active %}position-active{% else %}position-inactive{% endif %} {% if pos.direction == 'LONG' %}position-long{% elif pos.direction == 'SHORT' %}position-short{% endif %}">
            <div class="position-header">
                <span class="position-asset">{{ pos.asset }}</span>
                {% if pos.active %}
                <span class="position-direction {% if pos.direction == 'LONG' %}long{% else %}short{% endif %}">{{ pos.direction }}</span>
                {% else %}
                <span class="position-inactive-badge">NO POSITION</span>
                {% endif %}
            </div>
            {% if pos.active %}
            <div class="position-price">${{ "%.2f"|format(pos.current_price) }}</div>
            <div class="position-pnl {% if pos.pnl_pct >= 0 %}pnl-positive{% else %}pnl-negative{% endif %}">
                {{ "%+.2f"|format(pos.pnl_pct) }}%
            </div>
            <div class="position-levels">
                <div class="level-row">
                    <span class="level-label">Entry:</span>
                    <span class="level-value">${{ "%.2f"|format(pos.entry) }}</span>
                </div>
                <div class="level-row">
                    <span class="level-label">SL:</span>
                    <span class="level-value sl-value">${{ "%.2f"|format(pos.sl) }}</span>
                    {% if pos.trailing_active %}<span class="trailing-badge">TRAILING</span>{% endif %}
                    {% if pos.breakeven_active %}<span class="be-badge">BE</span>{% endif %}
                </div>
                <div class="level-row">
                    <span class="level-label">TP1:</span>
                    <span class="level-value {% if pos.tp1_hit %}tp-hit{% endif %}">${{ "%.2f"|format(pos.tp1) }}</span>
                    {% if pos.tp1_hit %}<span class="tp-badge">HIT</span>{% endif %}
                </div>
                <div class="level-row">
                    <span class="level-label">TP2:</span>
                    <span class="level-value {% if pos.tp2_hit %}tp-hit{% endif %}">${{ "%.2f"|format(pos.tp2) }}</span>
                    {% if pos.tp2_hit %}<span class="tp-badge">HIT</span>{% endif %}
                </div>
                <div class="level-row">
                    <span class="level-label">TP3:</span>
                    <span class="level-value">${{ "%.2f"|format(pos.tp3) }}</span>
                </div>
            </div>
            {% else %}
            <div class="position-waiting">Laukiama signalo...</div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    
    <div class="signals" style="margin-top: 30px;">
        <h2>📊 Recent Signals</h2>
        {% if signals %}
            {% for signal in signals %}
            <div class="signal signal-{{ signal.direction|lower }}">
                <div class="signal-info">
                    <span class="signal-asset">{{ signal.asset }}</span>
                    <span class="signal-direction {{ signal.direction|lower }}">{{ signal.direction }}</span>
                    <div class="signal-details">
                        Entry: ${{ "%.2f"|format(signal.price) }} | SL: ${{ "%.2f"|format(signal.sl) }} | TP1: ${{ "%.2f"|format(signal.tp1) }}
                    </div>
                    <div class="signal-details">
                        Score: {{ signal.score }} | Trend: {{ signal.trend }} | {{ signal.time }}
                    </div>
                </div>
            </div>
            {% endfor %}
        {% else %}
            <div class="no-signals">
                <p>No signals yet. Monitoring markets...</p>
            </div>
        {% endif %}
    </div>
    <script>
        async function updateDashboard() {
            try {
                const [statsRes, signalsRes] = await Promise.all([
                    fetch('/api/stats'),
                    fetch('/api/signals')
                ]);
                const stats = await statsRes.json();
                const signals = await signalsRes.json();
                
                document.querySelectorAll('.stat-value')[0].textContent = stats.total_signals;
                document.querySelectorAll('.stat-value')[1].textContent = stats.long_signals;
                document.querySelectorAll('.stat-value')[2].textContent = stats.short_signals;
                
                const container = document.querySelector('.signals');
                if (signals.length > 0) {
                    let html = '<h2>Recent Signals</h2>';
                    signals.forEach(s => {
                        const dir = s.direction.toLowerCase();
                        html += `<div class="signal signal-${dir}">
                            <div class="signal-info">
                                <span class="signal-asset">${s.asset}</span>
                                <span class="signal-direction ${dir}">${s.direction}</span>
                                <div class="signal-details">Entry: $${s.price.toFixed(2)} | SL: $${s.sl.toFixed(2)} | TP1: $${s.tp1.toFixed(2)}</div>
                                <div class="signal-details">Score: ${s.score} | Trend: ${s.trend} | ${s.time}</div>
                            </div>
                        </div>`;
                    });
                    container.innerHTML = html;
                }
            } catch(e) { console.log('Update error:', e); }
        }
        setInterval(updateDashboard, 30000);
    </script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    uptime = datetime.now(timezone.utc) - bot_stats["start_time"]
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    
    recent_signals = []
    for sig in reversed(signals_history[-20:]):
        recent_signals.append({
            "asset": ASSET_NAMES.get(sig['symbol'], sig['symbol']),
            "direction": sig['direction'],
            "price": sig['price'],
            "sl": sig['sl'],
            "tp1": sig['tp1'],
            "score": sig['score'],
            "trend": sig['trend'],
            "time": sig['time'].strftime('%H:%M UTC')
        })
    
    positions_data = []
    for symbol in FUTURES_ASSETS:
        asset_name = ASSET_NAMES.get(symbol, symbol)
        pos = open_positions.get(symbol)
        kraken_pos = kraken_positions['positions'].get(symbol)
        
        if pos:
            if pos['direction'] == "LONG":
                current_price = pos.get('highest_price', pos['entry_price'])
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            else:
                current_price = pos.get('lowest_price', pos['entry_price'])
                pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
            
            positions_data.append({
                "asset": asset_name,
                "active": True,
                "direction": pos['direction'],
                "entry": pos['entry_price'],
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "sl": pos['current_sl'],
                "tp1": pos['tp1'],
                "tp2": pos['tp2'],
                "tp3": pos['tp3'],
                "trailing_active": pos.get('trailing_active', False),
                "breakeven_active": pos.get('breakeven_active', False),
                "tp1_hit": pos.get('tp1_hit', False),
                "tp2_hit": pos.get('tp2_hit', False),
            })
        else:
            positions_data.append({
                "asset": asset_name,
                "active": False,
                "direction": None,
            })
    
    return render_template('index.html')

@app.route('/old')
def old_dashboard():
    """Senas dashboard su visa informacija"""
    uptime = datetime.now(timezone.utc) - bot_stats["start_time"]
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    
    recent_signals = []
    for sig in reversed(signals_history[-20:]):
        recent_signals.append({
            "asset": ASSET_NAMES.get(sig['symbol'], sig['symbol']),
            "direction": sig['direction'],
            "price": sig['price'],
            "sl": sig['sl'],
            "tp1": sig['tp1'],
            "score": sig['score'],
            "trend": sig['trend'],
            "time": sig['time'].strftime('%H:%M UTC')
        })
    
    positions_data = []
    for symbol in FUTURES_ASSETS:
        asset_name = ASSET_NAMES.get(symbol, symbol)
        pos = open_positions.get(symbol)
        
        if pos:
            if pos['direction'] == "LONG":
                current_price = pos.get('highest_price', pos['entry_price'])
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            else:
                current_price = pos.get('lowest_price', pos['entry_price'])
                pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
            
            positions_data.append({
                "asset": asset_name,
                "active": True,
                "direction": pos['direction'],
                "entry": pos['entry_price'],
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "sl": pos['current_sl'],
                "tp1": pos['tp1'],
                "tp2": pos['tp2'],
                "tp3": pos['tp3'],
                "trailing_active": pos.get('trailing_active', False),
                "breakeven_active": pos.get('breakeven_active', False),
                "tp1_hit": pos.get('tp1_hit', False),
                "tp2_hit": pos.get('tp2_hit', False),
            })
        else:
            positions_data.append({
                "asset": asset_name,
                "active": False,
                "direction": None,
            })
    
    return render_template_string(DASHBOARD_HTML, 
        stats=bot_stats,
        signals=recent_signals,
        positions=positions_data,
        uptime=f"{hours}h {minutes}m"
    )

@app.route('/api/stats')
def api_stats():
    balance = get_available_balance()
    open_pos_count = len([p for p in open_positions.values() if p])
    regime = market_regime_state.get("regime", "NEUTRAL")
    capital = balance.get("total_usd", 0)
    
    # Calculate risk limits (v8.9.18)
    daily_limit = capital * (DAILY_LOSS_LIMIT_PCT / 100) if capital > 0 else DAILY_LOSS_LIMIT_USD
    weekly_limit = capital * (WEEKLY_LOSS_LIMIT_PCT / 100) if capital > 0 else DAILY_LOSS_LIMIT_USD * 2.5
    
    daily_pnl = auto_trading_state.get("daily_pnl", 0)
    weekly_pnl = auto_trading_state.get("weekly_pnl", 0)
    
    win_rate_data = get_win_rate()
    return jsonify({
        "total_signals": win_rate_data.get("total_signals", bot_stats["total_signals"]),
        "long_signals": bot_stats["long_signals"],
        "short_signals": bot_stats["short_signals"],
        "wins": win_rate_data.get("wins", bot_stats["wins"]),
        "losses": win_rate_data.get("losses", bot_stats["losses"]),
        "win_rate": win_rate_data,
        "market_regime": regime,
        "open_positions": open_pos_count,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "last_check": bot_stats["last_check"].isoformat() if bot_stats["last_check"] else None,
        "balance": {
            "total_usd": balance["total_usd"],
            "cash_usd": balance["cash_usd"],
            "flex_usd": balance["flex_usd"],
        },
        "risk_limits": {
            "daily_limit_pct": DAILY_LOSS_LIMIT_PCT,
            "weekly_limit_pct": WEEKLY_LOSS_LIMIT_PCT,
            "daily_limit_usd": round(daily_limit, 2),
            "weekly_limit_usd": round(weekly_limit, 2),
            "daily_used_pct": round(abs(daily_pnl) / daily_limit * 100, 1) if daily_limit > 0 and daily_pnl < 0 else 0,
            "weekly_used_pct": round(abs(weekly_pnl) / weekly_limit * 100, 1) if weekly_limit > 0 and weekly_pnl < 0 else 0,
            "is_paused": auto_trading_state.get("is_paused", False),
            "pause_reason": auto_trading_state.get("pause_reason"),
            "pause_type": auto_trading_state.get("pause_type"),
        },
        "weekly_stats": {
            "trades": auto_trading_state.get("weekly_trades", 0),
            "wins": auto_trading_state.get("weekly_wins", 0),
            "losses": auto_trading_state.get("weekly_losses", 0),
        },
    })

@app.route('/api/balance')
def api_balance():
    """Gauti detalų Multi-Collateral balansą"""
    balance = fetch_multi_collateral_balance()
    return jsonify({
        "total_usd": balance["total_usd"],
        "cash_usd": balance["cash_usd"],
        "flex_usd": balance["flex_usd"],
        "collaterals": balance["collaterals"],
        "last_update": balance["last_update"].isoformat() if balance["last_update"] else None,
    })

@app.route('/qr')
def qr_code_image():
    """Generuoti QR kodą PWA diegimui"""
    domain = os.getenv("REPLIT_DEV_DOMAIN", "")
    if domain:
        url = f"https://{domain}"
    else:
        url = "https://futures-signals.replit.app"
    
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00d4aa", back_color="#1a1a2e")
    
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return Response(img_io.getvalue(), mimetype='image/png')

@app.route('/share')
def share_page():
    """PWA dalinimosi puslapis su QR kodu"""
    domain = os.getenv("REPLIT_DEV_DOMAIN", "")
    if domain:
        url = f"https://{domain}"
    else:
        url = "https://futures-signals.replit.app"
    
    share_html = '''
<!DOCTYPE html>
<html lang="lt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dalintis - Futures Signal Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: #fff;
        }
        .container {
            text-align: center;
            max-width: 400px;
        }
        h1 {
            font-size: 1.8rem;
            margin-bottom: 10px;
            background: linear-gradient(90deg, #00d4aa, #00a8ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle { color: #888; margin-bottom: 30px; }
        .qr-box {
            background: rgba(255,255,255,0.1);
            border-radius: 20px;
            padding: 20px;
            margin-bottom: 30px;
        }
        .qr-box img {
            width: 250px;
            height: 250px;
            border-radius: 10px;
        }
        .url-box {
            background: rgba(0,212,170,0.2);
            border: 1px solid #00d4aa;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            word-break: break-all;
            font-size: 0.9rem;
        }
        .instructions {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 20px;
            text-align: left;
            margin-bottom: 20px;
        }
        .instructions h3 {
            color: #00d4aa;
            margin-bottom: 15px;
            text-align: center;
        }
        .step {
            display: flex;
            align-items: flex-start;
            margin-bottom: 12px;
            gap: 10px;
        }
        .step-num {
            background: #00d4aa;
            color: #1a1a2e;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.8rem;
            flex-shrink: 0;
        }
        .step-text { color: #ccc; font-size: 0.9rem; }
        .btn {
            background: linear-gradient(90deg, #00d4aa, #00a8ff);
            color: #fff;
            border: none;
            padding: 15px 30px;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: bold;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .btn:hover { opacity: 0.9; }
        .features {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-top: 20px;
        }
        .feature {
            background: rgba(255,255,255,0.05);
            padding: 10px;
            border-radius: 8px;
            font-size: 0.8rem;
            color: #aaa;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Futures Signal Bot</h1>
        <p class="subtitle">Profesionalūs Crypto Signalai</p>
        
        <div class="qr-box">
            <img src="/qr" alt="QR Code">
        </div>
        
        <div class="url-box">''' + url + '''</div>
        
        <div class="instructions">
            <h3>📱 Kaip Įdiegti</h3>
            <div class="step">
                <span class="step-num">1</span>
                <span class="step-text">Nuskaitykite QR kodą telefono kamera</span>
            </div>
            <div class="step">
                <span class="step-num">2</span>
                <span class="step-text"><b>Android:</b> Spauskite "Įdiegti" arba "Add to Home Screen"</span>
            </div>
            <div class="step">
                <span class="step-num">3</span>
                <span class="step-text"><b>iPhone:</b> Share ➜ "Add to Home Screen"</span>
            </div>
            <div class="step">
                <span class="step-num">4</span>
                <span class="step-text">Programa bus įdiegta kaip native app!</span>
            </div>
        </div>
        
        <a href="/" class="btn">← Grįžti į Dashboard</a>
        
        <div class="features">
            <div class="feature">✅ Veikia offline</div>
            <div class="feature">✅ Push pranešimai</div>
            <div class="feature">✅ 8 Crypto assetai</div>
            <div class="feature">✅ Realiu laiku</div>
        </div>
    </div>
</body>
</html>
    '''
    return share_html

@app.route('/api/signals')
def api_signals():
    return jsonify([{
        "symbol": s['symbol'],
        "asset": ASSET_NAMES.get(s['symbol'], s['symbol']),
        "direction": s['direction'],
        "entry": s['price'],
        "price": s['price'],
        "sl": s['sl'],
        "tp1": s['tp1'],
        "score": s['score'],
        "time": s['time'].strftime('%H:%M UTC')
    } for s in reversed(signals_history[-20:])])

@app.route('/api/positions')
def api_positions():
    """Gauti atviras pozicijas su REALAUS LAIKO kainomis iš Kraken"""
    positions = []
    
    # Symbol mapping for ticker fetch
    TICKER_SYMBOLS = {
        "PF_XBTUSD": "BTC/USD:USD",
        "PF_ETHUSD": "ETH/USD:USD",
        "PF_SOLUSD": "SOL/USD:USD",
        "PF_XRPUSD": "XRP/USD:USD",
        "PF_LTCUSD": "LTC/USD:USD",
        "PF_ADAUSD": "ADA/USD:USD",
        "PF_DOTUSD": "DOT/USD:USD",
    }
    
    # Naudoti Kraken pozicijas (real-time duomenys)
    for symbol, kraken_pos in kraken_positions.get('positions', {}).items():
        if not kraken_pos:
            continue
        
        asset_name = ASSET_NAMES.get(symbol, symbol)
        entry_price = kraken_pos.get('entry_price', 0)
        direction = kraken_pos.get('direction', 'LONG')
        leverage = kraken_pos.get('leverage', 1)
        size_usd = kraken_pos.get('size_usd', 25)
        contracts = kraken_pos.get('size', 0)
        
        # Gauti dabartinę kainą per ticker
        try:
            ticker_symbol = TICKER_SYMBOLS.get(symbol)
            if ticker_symbol:
                ticker = exchange.fetch_ticker(ticker_symbol)
                current_price = ticker.get('last', entry_price) or entry_price
            else:
                current_price = entry_price
        except:
            current_price = entry_price
        
        # Skaičiuoti P&L
        if entry_price > 0 and contracts > 0:
            if direction == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price * 100
                pnl_usd = (current_price - entry_price) * contracts
            else:
                pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_usd = (entry_price - current_price) * contracts
        else:
            pnl_pct = 0
            pnl_usd = 0
        
        positions.append({
            "asset": asset_name,
            "side": direction,
            "entry": entry_price,
            "current": current_price,
            "pnl": pnl_usd,
            "pnl_pct": pnl_pct,
            "size": size_usd,
            "leverage": leverage,
        })
    
    return jsonify(positions)

@app.route('/api/bot/status')
def api_bot_status():
    """Gauti boto statusą"""
    return jsonify({
        "auto_trading": AUTO_TRADING_ENABLED,
        "is_paused": auto_trading_state.get("is_paused", False),
        "pause_reason": auto_trading_state.get("pause_reason"),
        "active_assets": [a for a in FUTURES_ASSETS if a not in disabled_assets],
        "disabled_assets": list(disabled_assets),
        "open_positions": len([p for p in open_positions.values() if p]),
    })

@app.route('/api/bot/stop', methods=['POST'])
def api_bot_stop():
    """Sustabdyti botą (pauzė)"""
    global auto_trading_state
    auto_trading_state['is_paused'] = True
    auto_trading_state['pause_reason'] = "Rankinė pauzė (PWA)"
    auto_trading_state['pause_type'] = "MANUAL"
    print("🛑 BOT STOPPED via PWA")
    return jsonify({"success": True, "message": "Botas sustabdytas"})

@app.route('/api/bot/start', methods=['POST'])
def api_bot_start():
    """Paleisti botą"""
    global auto_trading_state
    auto_trading_state['is_paused'] = False
    auto_trading_state['pause_reason'] = None
    auto_trading_state['pause_type'] = None
    print("▶️ BOT STARTED via PWA")
    return jsonify({"success": True, "message": "Botas paleistas"})

@app.route('/api/bot/toggle_asset', methods=['POST'])
def api_toggle_asset():
    """Įjungti/išjungti asset'ą"""
    global disabled_assets
    data = request.get_json() or {}
    symbol = data.get('symbol')
    
    if not symbol:
        return jsonify({"success": False, "error": "No symbol provided"}), 400
    
    if symbol in disabled_assets:
        disabled_assets.discard(symbol)
        action = "enabled"
        print(f"✅ Asset ENABLED: {symbol}")
    else:
        disabled_assets.add(symbol)
        action = "disabled"
        print(f"❌ Asset DISABLED: {symbol}")
    
    return jsonify({
        "success": True,
        "symbol": symbol,
        "action": action,
        "disabled_assets": list(disabled_assets)
    })

@app.route('/emergency/close_all', methods=['POST', 'GET'])
async def emergency_close_all():
    """🚨 EMERGENCY: Uždaryti visas pozicijas DABAR"""
    global auto_trading_state
    
    print("\n" + "="*50)
    print("🚨🚨🚨 EMERGENCY CLOSE ALL TRIGGERED 🚨🚨🚨")
    print("="*50)
    
    auto_trading_state['is_paused'] = True
    auto_trading_state['pause_reason'] = "EMERGENCY STOP"
    auto_trading_state['pause_type'] = "EMERGENCY"
    
    log_risk_event("EMERGENCY_STOP", "Manual emergency close all triggered", "critical")
    
    closed = []
    errors = []
    
    for symbol in FUTURES_ASSETS:
        if has_open_position(symbol):
            try:
                result = await close_full_position(symbol, reason="EMERGENCY_CLOSE")
                if result.get('success'):
                    closed.append(symbol)
                    print(f"  ✅ Closed: {symbol}")
                else:
                    errors.append(f"{symbol}: {result.get('reason')}")
            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")
                print(f"  ❌ Error closing {symbol}: {e}")
    
    # Send emergency notification via Telegram
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            from telegram import Bot
            tg_bot = Bot(token=TELEGRAM_TOKEN)
            await tg_bot.send_message(
                chat_id=CHAT_ID,
                text=f"""🚨 EMERGENCY CLOSE ALL

Uždarytos pozicijos: {len(closed)}
{', '.join([ASSET_NAMES.get(s, s) for s in closed]) if closed else 'Nėra'}

Klaidos: {len(errors)}
{chr(10).join(errors) if errors else 'Nėra'}

Botas SUSTABDYTAS."""
            )
        except Exception as e:
            print(f"⚠️ Telegram error: {e}")
    
    return jsonify({
        "success": True,
        "closed": closed,
        "errors": errors,
        "bot_paused": True
    })

@app.route('/api/quant')
def api_quant():
    global quant_results, quant_correlation, quant_last_update
    
    # v8.9.2: Faster quant refresh (15 minutes instead of 1 hour)
    QUANT_REFRESH_INTERVAL = 900  # 15 minutes
    need_refresh = (quant_last_update is None or 
                    (datetime.now() - quant_last_update).seconds > QUANT_REFRESH_INTERVAL)
    
    if need_refresh:
        try:
            print("\n🧮 Paleidžiama matematinė analizė (4 metų duomenys)...")
            quant_results, quant_correlation = quant_engine.run_all_assets()
            quant_last_update = datetime.now()
            print("✅ Matematinė analizė baigta!")
        except Exception as e:
            print(f"Quant error: {e}")
            return jsonify({"error": str(e)}), 500
    
    summary = {}
    for asset, data in quant_results.items():
        if data:
            bias, signals = quant_engine.get_quant_signal_bias(data)
            mc7 = data.get('monte_carlo_7d', {})
            mc30 = data.get('monte_carlo_30d', {})
            mr = data.get('mean_reversion', {})
            fib = data.get('fibonacci', {})
            
            summary[asset] = {
                "current_price": mc7.get('current_price', 0),
                "signal_bias": bias,
                "signals": signals,
                "direction": "LONG" if bias > 0 else "SHORT" if bias < 0 else "NEUTRAL",
                "prob_up_7d": mc7.get('prob_up', 0.5),
                "prob_down_7d": mc7.get('prob_down', 0.5),
                "expected_7d": mc7.get('expected_price', 0),
                "expected_30d": mc30.get('expected_price', 0),
                "prob_up_10_30d": mc30.get('prob_up_10%', 0),
                "prob_down_10_30d": mc30.get('prob_down_10%', 0),
                "mean_reversion_price": mr.get('mean_price', 0),
                "deviation_std": mr.get('deviation_std', 0),
                "fibonacci_zone": fib.get('zone', 'NEUTRAL'),
                "fibonacci_bias": fib.get('bias', 'NEUTRAL'),
                "annual_volatility": data.get('returns_analysis', {}).get('annual_volatility', 0),
            }
    
    return jsonify({
        "assets": summary,
        "correlation": quant_correlation,
        "last_update": quant_last_update.isoformat() if quant_last_update else None,
    })

@app.route('/api/sentiment')
def api_sentiment():
    """Gauti sentimento analizės duomenis"""
    try:
        data = sentiment_analyzer.get_all_sentiments()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/onchain')
def api_onchain():
    """Gauti on-chain analizės duomenis"""
    try:
        data = onchain_analytics.get_all_analytics()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ml/stats')
def api_ml_stats():
    """Gauti ML modelio statistiką"""
    try:
        stats = ml_predictor.get_model_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ml/train', methods=['POST'])
def api_ml_train():
    """Treniruoti ML modelį"""
    try:
        result = ml_predictor.train()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test-signal')
def api_test_signal():
    """Siųsti testinį Candle Reversal signalą į Telegram"""
    import asyncio
    
    test_signal = {
        "symbol": "PF_XBTUSD",
        "direction": "SHORT",
        "score": 75,
        "base_score": 60,
        "signals": ["CANDLE_REVERSAL", "RSI_SHORT", "DAILY_RESISTANCE", "5 green + 2 red candles pattern", "RSI=72.5 >= 70 (overbought)"],
        "price": 88000.00,
        "sl": 88500.00,
        "tp1": 87200.00,
        "tp2": 86400.00,
        "tp3": 85200.00,
        "atr": 400.0,
        "rsi": 72.5,
        "trend": "BULL",
        "confidence": 0.7,
        "modules_used": 1,
        "entry_type": "MARKET",
        "entry_reason": "⚡ Candle Reversal pattern",
        "time": datetime.now(timezone.utc)
    }
    
    try:
        asyncio.run(send_telegram_signal(test_signal))
        return jsonify({"success": True, "message": "Test signal sent to Telegram"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================================
# WIN/LOSS TRACKING API
# ================================
@app.route('/api/signals/pending')
def api_signals_pending():
    """Gauti nepažymėtus signalus"""
    data = load_signal_results()
    pending = [s for s in data["signals"] if s.get("result") is None]
    return jsonify({
        "pending_signals": pending[-20:],  # Paskutiniai 20
        "total_pending": len(pending)
    })

@app.route('/api/signals/mark', methods=['POST'])
def api_mark_signal():
    """
    Pažymėti signalą kaip WIN arba LOSS
    Body: {"signal_id": "BTC_20241216_123456", "result": "WIN", "profit_pct": 2.5}
    """
    try:
        data = request.get_json()
        signal_id = data.get('signal_id')
        result = data.get('result', '').upper()
        profit_pct = float(data.get('profit_pct', 0))
        
        if not signal_id:
            return jsonify({"error": "signal_id required"}), 400
        if result not in ['WIN', 'LOSS']:
            return jsonify({"error": "result must be WIN or LOSS"}), 400
        
        success, sig = mark_signal_result(signal_id, result, profit_pct)
        
        if success:
            return jsonify({
                "success": True,
                "signal": sig,
                "stats": get_win_rate()
            })
        else:
            return jsonify({"error": "Signal not found"}), 404
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/signals/stats')
def api_signal_stats():
    """Gauti signalų statistiką (win rate)"""
    stats = get_win_rate()
    data = load_signal_results()
    
    # Paskutiniai 10 pažymėtų signalų
    marked = [s for s in data["signals"] if s.get("result") is not None]
    recent_marked = marked[-10:]
    
    return jsonify({
        "stats": stats,
        "recent_results": recent_marked,
        "ml_ready": stats["total"] >= 50
    })

@app.route('/api/analytics')
def api_analytics():
    """
    🧠 Pilna Analytics Sistema - Score, Asset, Session analizė
    """
    data = load_signal_results()
    marked_signals = [s for s in data["signals"] if s.get("result") is not None]
    
    if len(marked_signals) < 3:
        return jsonify({
            "error": "Per mažai duomenų (reikia min 3 pažymėtų signalų)",
            "signals_count": len(marked_signals)
        })
    
    # ========================================
    # 🧠 SCORE ANALYTICS
    # ========================================
    score_ranges = {
        "60-69": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
        "70-74": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
        "75-79": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
        "80-84": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
        "85-89": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
        "90+": {"wins": 0, "losses": 0, "pnl": 0, "signals": []},
    }
    
    for sig in marked_signals:
        score = sig.get("score", 0)
        result = sig.get("result")
        pnl = sig.get("profit_pct", 0) or 0
        
        if score < 70:
            rng = "60-69"
        elif score < 75:
            rng = "70-74"
        elif score < 80:
            rng = "75-79"
        elif score < 85:
            rng = "80-84"
        elif score < 90:
            rng = "85-89"
        else:
            rng = "90+"
        
        if result == "WIN":
            score_ranges[rng]["wins"] += 1
        else:
            score_ranges[rng]["losses"] += 1
        score_ranges[rng]["pnl"] += pnl
        score_ranges[rng]["signals"].append(pnl)
    
    score_analytics = {}
    for rng, stats in score_ranges.items():
        total = stats["wins"] + stats["losses"]
        if total > 0:
            win_rate = (stats["wins"] / total) * 100
            avg_pnl = stats["pnl"] / total
            max_dd = min(stats["signals"]) if stats["signals"] else 0
            score_analytics[rng] = {
                "total": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(win_rate, 1),
                "avg_pnl": round(avg_pnl, 2),
                "max_drawdown": round(max_dd, 2),
                "recommendation": "✅ GOOD" if win_rate >= 55 else "⚠️ WEAK" if win_rate >= 45 else "❌ BAD"
            }
    
    # ========================================
    # 📊 ASSET ANALYTICS
    # ========================================
    asset_stats = {}
    for asset in ASSET_NAMES.values():
        asset_stats[asset] = {"wins": 0, "losses": 0, "pnl": 0, "signals": []}
    
    for sig in marked_signals:
        symbol = sig.get("symbol", "")
        asset = ASSET_NAMES.get(symbol, symbol.replace("PF_", "").replace("USD", ""))
        result = sig.get("result")
        pnl = sig.get("profit_pct", 0) or 0
        
        if asset not in asset_stats:
            asset_stats[asset] = {"wins": 0, "losses": 0, "pnl": 0, "signals": []}
        
        if result == "WIN":
            asset_stats[asset]["wins"] += 1
        else:
            asset_stats[asset]["losses"] += 1
        asset_stats[asset]["pnl"] += pnl
        asset_stats[asset]["signals"].append(pnl)
    
    asset_analytics = {}
    for asset, stats in asset_stats.items():
        total = stats["wins"] + stats["losses"]
        if total > 0:
            win_rate = (stats["wins"] / total) * 100
            avg_pnl = stats["pnl"] / total
            total_pnl = stats["pnl"]
            max_dd = min(stats["signals"]) if stats["signals"] else 0
            
            # Profit Factor
            wins_sum = sum([p for p in stats["signals"] if p > 0])
            losses_sum = abs(sum([p for p in stats["signals"] if p < 0]))
            pf = wins_sum / losses_sum if losses_sum > 0 else wins_sum if wins_sum > 0 else 0
            
            asset_analytics[asset] = {
                "total": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(win_rate, 1),
                "avg_pnl": round(avg_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown": round(max_dd, 2),
                "recommendation": "✅ ALPHA" if pf >= 1.5 else "⚠️ OK" if pf >= 1.0 else "❌ DISABLE"
            }
    
    # ========================================
    # ⏱️ SESSION ANALYTICS
    # ========================================
    sessions = {
        "Asia (00-08 UTC)": {"wins": 0, "losses": 0, "pnl": 0},
        "London (08-13 UTC)": {"wins": 0, "losses": 0, "pnl": 0},
        "NY (13-21 UTC)": {"wins": 0, "losses": 0, "pnl": 0},
        "Overlap (13-17 UTC)": {"wins": 0, "losses": 0, "pnl": 0},
        "Night (21-00 UTC)": {"wins": 0, "losses": 0, "pnl": 0},
    }
    
    for sig in marked_signals:
        time_str = sig.get("time", "")
        result = sig.get("result")
        pnl = sig.get("profit_pct", 0) or 0
        
        try:
            sig_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            hour = sig_time.hour
        except:
            continue
        
        if 0 <= hour < 8:
            session = "Asia (00-08 UTC)"
        elif 8 <= hour < 13:
            session = "London (08-13 UTC)"
        elif 13 <= hour < 17:
            session = "Overlap (13-17 UTC)"
            # Also count in NY
            if result == "WIN":
                sessions["NY (13-21 UTC)"]["wins"] += 1
            else:
                sessions["NY (13-21 UTC)"]["losses"] += 1
            sessions["NY (13-21 UTC)"]["pnl"] += pnl
        elif 17 <= hour < 21:
            session = "NY (13-21 UTC)"
        else:
            session = "Night (21-00 UTC)"
        
        if result == "WIN":
            sessions[session]["wins"] += 1
        else:
            sessions[session]["losses"] += 1
        sessions[session]["pnl"] += pnl
    
    session_analytics = {}
    for session, stats in sessions.items():
        total = stats["wins"] + stats["losses"]
        if total > 0:
            win_rate = (stats["wins"] / total) * 100
            session_analytics[session] = {
                "total": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(win_rate, 1),
                "total_pnl": round(stats["pnl"], 2),
                "recommendation": "✅ TRADE" if win_rate >= 55 else "⚠️ CAREFUL" if win_rate >= 45 else "❌ AVOID"
            }
    
    # ========================================
    # 📈 RECOMMENDATIONS
    # ========================================
    recommendations = []
    
    # Score recommendations
    best_score_range = max(score_analytics.items(), key=lambda x: x[1]["win_rate"], default=(None, None))
    if best_score_range[0]:
        recommendations.append(f"🎯 Geriausias score diapazonas: {best_score_range[0]} ({best_score_range[1]['win_rate']}% win rate)")
    
    # Asset recommendations
    alpha_assets = [a for a, s in asset_analytics.items() if s.get("profit_factor", 0) >= 1.5]
    weak_assets = [a for a, s in asset_analytics.items() if s.get("profit_factor", 0) < 1.0]
    if alpha_assets:
        recommendations.append(f"✅ Alpha asset'ai: {', '.join(alpha_assets)}")
    if weak_assets:
        recommendations.append(f"❌ Išjungti: {', '.join(weak_assets)}")
    
    # Session recommendations
    best_session = max(session_analytics.items(), key=lambda x: x[1]["win_rate"], default=(None, None))
    if best_session[0]:
        recommendations.append(f"⏰ Geriausia sesija: {best_session[0]} ({best_session[1]['win_rate']}% win rate)")
    
    return jsonify({
        "score_analytics": score_analytics,
        "asset_analytics": asset_analytics,
        "session_analytics": session_analytics,
        "recommendations": recommendations,
        "total_signals_analyzed": len(marked_signals),
        "generated_at": datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/risk_events')
def api_risk_events():
    """
    ⚠️ Risk Event Analytics - Track when bot should have stopped
    Returns: daily_limits, weekly_limits, consecutive_losses, volatility_spikes, emergency_stops
    """
    events = risk_events_log.copy()
    
    # Group by type
    by_type = {
        "DAILY_LIMIT": [],
        "WEEKLY_LIMIT": [],
        "CONSECUTIVE_LOSSES": [],
        "VOLATILITY_SPIKE": [],
        "EMERGENCY_STOP": [],
    }
    
    for event in events:
        event_type = event.get("type", "OTHER")
        if event_type in by_type:
            by_type[event_type].append(event)
    
    # Summary stats
    summary = {
        "total_events": len(events),
        "critical_events": len([e for e in events if e.get("severity") == "critical"]),
        "warning_events": len([e for e in events if e.get("severity") == "warning"]),
        "daily_limit_hits": len(by_type["DAILY_LIMIT"]),
        "weekly_limit_hits": len(by_type["WEEKLY_LIMIT"]),
        "consecutive_loss_events": len(by_type["CONSECUTIVE_LOSSES"]),
        "volatility_spikes": len(by_type["VOLATILITY_SPIKE"]),
        "emergency_stops": len(by_type["EMERGENCY_STOP"]),
    }
    
    # Timeline for chart
    timeline = []
    for event in events[-50:]:  # Last 50 events for chart
        timeline.append({
            "timestamp": event.get("timestamp"),
            "type": event.get("type"),
            "severity": event.get("severity"),
            "capital": event.get("capital_at_event", 0),
            "daily_pnl": event.get("daily_pnl", 0),
            "weekly_pnl": event.get("weekly_pnl", 0),
        })
    
    return jsonify({
        "summary": summary,
        "events_by_type": by_type,
        "timeline": timeline,
        "all_events": events[-100:],
        "generated_at": datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/heatmap')
def api_heatmap():
    """
    📊 Strategy Heatmap - Score x Volatility x PnL
    X axis: Score ranges (60-69, 70-74, 75-79, 80-84, 85+)
    Y axis: Volatility levels (LOW, MEDIUM, HIGH)
    Color: Average PnL
    """
    data = load_signal_results()
    marked_signals = [s for s in data["signals"] if s.get("result") is not None]
    
    if len(marked_signals) < 3:
        return jsonify({
            "error": "Per mažai duomenų (reikia min 3 signalų)",
            "signals_count": len(marked_signals)
        })
    
    # Define grid
    score_ranges = ["60-69", "70-74", "75-79", "80-84", "85+"]
    volatility_levels = ["LOW", "MEDIUM", "HIGH"]
    
    # Initialize heatmap grid
    heatmap = {}
    for vol in volatility_levels:
        heatmap[vol] = {}
        for score in score_ranges:
            heatmap[vol][score] = {"pnl_sum": 0, "count": 0, "wins": 0, "losses": 0}
    
    # Classify signals
    for sig in marked_signals:
        score = sig.get("score", 0)
        pnl = sig.get("profit_pct", 0) or 0
        result = sig.get("result")
        
        # Determine score range
        if score < 70:
            score_key = "60-69"
        elif score < 75:
            score_key = "70-74"
        elif score < 80:
            score_key = "75-79"
        elif score < 85:
            score_key = "80-84"
        else:
            score_key = "85+"
        
        # Determine volatility from ATR or use default
        atr_pct = sig.get("atr_pct", 0) or sig.get("volatility", 0) or 0
        if atr_pct == 0:
            # Estimate from SL distance
            entry = sig.get("entry", 0)
            sl = sig.get("sl", 0)
            if entry > 0 and sl > 0:
                atr_pct = abs(entry - sl) / entry * 100
        
        # Classify volatility
        if atr_pct < 1.5:
            vol_key = "LOW"
        elif atr_pct < 3.0:
            vol_key = "MEDIUM"
        else:
            vol_key = "HIGH"
        
        # Add to grid
        heatmap[vol_key][score_key]["pnl_sum"] += pnl
        heatmap[vol_key][score_key]["count"] += 1
        if result == "WIN":
            heatmap[vol_key][score_key]["wins"] += 1
        else:
            heatmap[vol_key][score_key]["losses"] += 1
    
    # Calculate averages and format for frontend
    cells = []
    for vol_idx, vol in enumerate(volatility_levels):
        for score_idx, score in enumerate(score_ranges):
            cell = heatmap[vol][score]
            avg_pnl = cell["pnl_sum"] / cell["count"] if cell["count"] > 0 else None
            win_rate = (cell["wins"] / cell["count"] * 100) if cell["count"] > 0 else None
            
            cells.append({
                "x": score_idx,
                "y": vol_idx,
                "score_range": score,
                "volatility": vol,
                "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
                "win_rate": round(win_rate, 1) if win_rate is not None else None,
                "count": cell["count"],
                "wins": cell["wins"],
                "losses": cell["losses"]
            })
    
    # Find best and worst cells
    valid_cells = [c for c in cells if c["count"] > 0]
    best_cell = max(valid_cells, key=lambda x: x["avg_pnl"]) if valid_cells else None
    worst_cell = min(valid_cells, key=lambda x: x["avg_pnl"]) if valid_cells else None
    
    return jsonify({
        "cells": cells,
        "score_ranges": score_ranges,
        "volatility_levels": volatility_levels,
        "best_zone": best_cell,
        "worst_zone": worst_cell,
        "total_signals": len(marked_signals),
        "generated_at": datetime.now(timezone.utc).isoformat()
    })

@app.route('/api/signals/mark/last', methods=['POST'])
def api_mark_last_signal():
    """
    Pažymėti paskutinį signalą kaip WIN arba LOSS
    Body: {"result": "WIN", "profit_pct": 2.5}
    """
    try:
        req_data = request.get_json()
        result = req_data.get('result', '').upper()
        profit_pct = float(req_data.get('profit_pct', 0))
        
        if result not in ['WIN', 'LOSS']:
            return jsonify({"error": "result must be WIN or LOSS"}), 400
        
        data = load_signal_results()
        pending = [s for s in data["signals"] if s.get("result") is None]
        
        if not pending:
            return jsonify({"error": "No pending signals"}), 404
        
        last_signal = pending[-1]
        success, sig = mark_signal_result(last_signal["id"], result, profit_pct)
        
        if success:
            return jsonify({
                "success": True,
                "signal": sig,
                "stats": get_win_rate()
            })
        else:
            return jsonify({"error": "Failed to mark signal"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/advanced')
def advanced_dashboard():
    """Pažangios analizės dashboard"""
    return render_template_string(ADVANCED_HTML)

@app.route('/quant')
def quant_dashboard():
    return render_template_string(QUANT_HTML)

ADVANCED_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>AI + On-Chain Analize</title>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0a0a0f; color: #fff; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { color: #9945FF; font-size: 2em; }
        .section { background: #1a1a2e; border-radius: 15px; padding: 20px; margin-bottom: 20px; }
        .section h2 { color: #9945FF; margin-bottom: 15px; font-size: 1.3em; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; }
        .card { background: #252540; padding: 15px; border-radius: 10px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .asset-name { font-size: 1.3em; font-weight: bold; }
        .badge { padding: 4px 12px; border-radius: 15px; font-size: 0.8em; font-weight: bold; }
        .badge-bullish { background: #00ff8830; color: #00ff88; }
        .badge-bearish { background: #ff446630; color: #ff4466; }
        .badge-neutral { background: #88888830; color: #888; }
        .stat-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #333; font-size: 0.9em; }
        .stat-label { color: #888; }
        .stat-value { font-weight: bold; }
        .fear-greed { text-align: center; padding: 20px; }
        .fg-value { font-size: 3em; font-weight: bold; }
        .fg-label { color: #888; margin-top: 5px; }
        .ml-status { padding: 15px; background: #252540; border-radius: 10px; }
        .loading { text-align: center; color: #888; padding: 30px; }
        .back-link { display: inline-block; margin-bottom: 20px; color: #9945FF; text-decoration: none; }
    </style>
</head>
<body>
    <a href="/" class="back-link">< Atgal i signalus</a>
    
    <div class="header">
        <h1>AI + On-Chain Analize</h1>
        <p>Sentimentas | Bangines | ML Modelis</p>
    </div>
    
    <div class="section">
        <h2>Fear & Greed Indeksas</h2>
        <div class="fear-greed" id="fear-greed">
            <div class="loading">Krauna...</div>
        </div>
    </div>
    
    <div class="section">
        <h2>Reddit Sentimentas</h2>
        <div class="grid" id="sentiment-grid">
            <div class="loading">Krauna sentimento duomenis...</div>
        </div>
    </div>
    
    <div class="section">
        <h2>On-Chain Metrikos (Bangines)</h2>
        <div class="grid" id="onchain-grid">
            <div class="loading">Krauna on-chain duomenis...</div>
        </div>
    </div>
    
    <div class="section">
        <h2>ML Modelio Statusas</h2>
        <div class="ml-status" id="ml-status">
            <div class="loading">Krauna ML statistika...</div>
        </div>
    </div>
    
    <script>
        async function loadData() {
            try {
                const [sentimentRes, onchainRes, mlRes] = await Promise.all([
                    fetch('/api/sentiment'),
                    fetch('/api/onchain'),
                    fetch('/api/ml/stats')
                ]);
                
                const sentiment = await sentimentRes.json();
                const onchain = await onchainRes.json();
                const ml = await mlRes.json();
                
                // Fear & Greed
                if (sentiment.fear_greed) {
                    const fg = sentiment.fear_greed;
                    let color = '#888';
                    if (fg.value >= 55) color = '#00ff88';
                    else if (fg.value <= 45) color = '#ff4466';
                    
                    document.getElementById('fear-greed').innerHTML = `
                        <div class="fg-value" style="color: ${color}">${fg.value}</div>
                        <div class="fg-label">${fg.label}</div>
                    `;
                }
                
                // Sentiment
                let sentimentHtml = '';
                for (const [symbol, data] of Object.entries(sentiment)) {
                    if (symbol === 'fear_greed') continue;
                    const badgeClass = data.sentiment_label.includes('BULL') ? 'badge-bullish' : 
                                       data.sentiment_label.includes('BEAR') ? 'badge-bearish' : 'badge-neutral';
                    sentimentHtml += `
                        <div class="card">
                            <div class="card-header">
                                <span class="asset-name">${symbol}</span>
                                <span class="badge ${badgeClass}">${data.sentiment_label}</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Sentimento Balas</span>
                                <span class="stat-value">${(data.sentiment_score * 100).toFixed(1)}%</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Analizuota Postu</span>
                                <span class="stat-value">${data.posts_analyzed}</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Saltinis</span>
                                <span class="stat-value">${data.source}</span>
                            </div>
                        </div>
                    `;
                }
                document.getElementById('sentiment-grid').innerHTML = sentimentHtml;
                
                // On-Chain
                let onchainHtml = '';
                for (const [symbol, data] of Object.entries(onchain)) {
                    const badgeClass = data.overall_signal === 'BULLISH' ? 'badge-bullish' : 
                                       data.overall_signal === 'BEARISH' ? 'badge-bearish' : 'badge-neutral';
                    const whale = data.whale_activity;
                    
                    onchainHtml += `
                        <div class="card">
                            <div class="card-header">
                                <span class="asset-name">${symbol}</span>
                                <span class="badge ${badgeClass}">${data.overall_signal}</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Exchange Inflow</span>
                                <span class="stat-value" style="color: #ff4466;">$${(whale.exchange_inflow_usd/1000000).toFixed(1)}M</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Exchange Outflow</span>
                                <span class="stat-value" style="color: #00ff88;">$${(whale.exchange_outflow_usd/1000000).toFixed(1)}M</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Net Flow</span>
                                <span class="stat-value" style="color: ${whale.net_flow_usd >= 0 ? '#00ff88' : '#ff4466'}">$${(whale.net_flow_usd/1000000).toFixed(1)}M</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">Bangines Signalas</span>
                                <span class="stat-value">${whale.signal}</span>
                            </div>
                            <div class="stat-row">
                                <span class="stat-label">On-Chain Balas</span>
                                <span class="stat-value">${data.onchain_score}</span>
                            </div>
                        </div>
                    `;
                }
                document.getElementById('onchain-grid').innerHTML = onchainHtml;
                
                // ML Status
                document.getElementById('ml-status').innerHTML = `
                    <div class="stat-row">
                        <span class="stat-label">Modelis Aktyvus</span>
                        <span class="stat-value">${ml.model_exists ? 'Taip' : 'Ne (reikia treniravimo)'}</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Treniravimo Signalai</span>
                        <span class="stat-value">${ml.training_samples}</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Pazymeti Signalai</span>
                        <span class="stat-value">${ml.labeled_samples} / 50 (min)</span>
                    </div>
                    <p style="color: #666; font-size: 0.85em; margin-top: 15px;">
                        ML modelis bus treniruojamas automatiskai kai bus 50+ pazymetu signalu (win/loss)
                    </p>
                `;
                
            } catch (error) {
                console.error('Error loading data:', error);
            }
        }
        
        loadData();
        setInterval(loadData, 60000);
    </script>
</body>
</html>
"""

QUANT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Matematine Analize</title>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0a0a0f; color: #fff; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { color: #ffd700; font-size: 2em; }
        .loading { text-align: center; padding: 50px; }
        .assets { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .asset-card { background: #1a1a2e; border-radius: 15px; padding: 20px; }
        .asset-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .asset-name { font-size: 1.5em; font-weight: bold; }
        .direction { padding: 5px 15px; border-radius: 20px; font-weight: bold; }
        .direction.LONG { background: #00ff8830; color: #00ff88; }
        .direction.SHORT { background: #ff446630; color: #ff4466; }
        .direction.NEUTRAL { background: #88888830; color: #888; }
        .stat-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #333; }
        .stat-label { color: #888; }
        .stat-value { font-weight: bold; }
        .prob-bar { height: 20px; background: #333; border-radius: 10px; overflow: hidden; margin: 5px 0; }
        .prob-fill { height: 100%; transition: width 0.5s; }
        .prob-up { background: linear-gradient(90deg, #00aa55, #00ff88); }
        .prob-down { background: linear-gradient(90deg, #ff4466, #aa2244); }
        .section-title { color: #ffd700; margin: 15px 0 10px 0; font-size: 0.9em; }
        .bias-score { font-size: 2em; text-align: center; margin: 10px 0; }
        .bias-positive { color: #00ff88; }
        .bias-negative { color: #ff4466; }
        .bias-neutral { color: #888; }
        .signals-list { font-size: 0.8em; color: #aaa; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧮 Matematinė Analizė - 4 Metų Duomenys</h1>
        <p>Monte Carlo | ARIMA | Mean Reversion | Fibonacci</p>
        <p id="last-update" style="color: #666; margin-top: 10px;"></p>
    </div>
    
    <div class="loading" id="loading">
        <p>⏳ Analizuojami 4 metų duomenys...</p>
        <p style="color: #666; margin-top: 10px;">Tai gali užtrukti ~30 sekundžių</p>
    </div>
    
    <div class="assets" id="assets" style="display: none;"></div>
    
    <script>
        async function loadQuant() {
            try {
                const response = await fetch('/api/quant');
                const data = await response.json();
                
                document.getElementById('loading').style.display = 'none';
                document.getElementById('assets').style.display = 'grid';
                
                if (data.last_update) {
                    document.getElementById('last-update').textContent = 
                        'Atnaujinta: ' + new Date(data.last_update).toLocaleString('lt-LT');
                }
                
                const assetsDiv = document.getElementById('assets');
                assetsDiv.innerHTML = '';
                
                for (const [asset, info] of Object.entries(data.assets)) {
                    const biasClass = info.signal_bias > 0 ? 'bias-positive' : 
                                     info.signal_bias < 0 ? 'bias-negative' : 'bias-neutral';
                    
                    const card = document.createElement('div');
                    card.className = 'asset-card';
                    card.innerHTML = `
                        <div class="asset-header">
                            <span class="asset-name">${asset}</span>
                            <span class="direction ${info.direction}">${info.direction}</span>
                        </div>
                        
                        <div class="stat-row">
                            <span class="stat-label">Dabartinė kaina</span>
                            <span class="stat-value">$${info.current_price.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
                        </div>
                        
                        <div class="bias-score ${biasClass}">
                            ${info.signal_bias > 0 ? '+' : ''}${info.signal_bias}
                        </div>
                        <div class="signals-list">Signalai: ${info.signals.join(', ') || 'Nėra'}</div>
                        
                        <div class="section-title">📊 Monte Carlo (7 dienų)</div>
                        <div class="stat-row">
                            <span class="stat-label">Tikimybė UP</span>
                            <span class="stat-value" style="color: #00ff88;">${(info.prob_up_7d * 100).toFixed(1)}%</span>
                        </div>
                        <div class="prob-bar"><div class="prob-fill prob-up" style="width: ${info.prob_up_7d * 100}%"></div></div>
                        
                        <div class="stat-row">
                            <span class="stat-label">Tikimybė DOWN</span>
                            <span class="stat-value" style="color: #ff4466;">${(info.prob_down_7d * 100).toFixed(1)}%</span>
                        </div>
                        <div class="prob-bar"><div class="prob-fill prob-down" style="width: ${info.prob_down_7d * 100}%"></div></div>
                        
                        <div class="stat-row">
                            <span class="stat-label">Tikėtina kaina (7d)</span>
                            <span class="stat-value">$${info.expected_7d.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
                        </div>
                        
                        <div class="section-title">📈 30 Dienų Prognozė</div>
                        <div class="stat-row">
                            <span class="stat-label">Tikėtina kaina</span>
                            <span class="stat-value">$${info.expected_30d.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Tikimybė +10%</span>
                            <span class="stat-value" style="color: #00ff88;">${(info.prob_up_10_30d * 100).toFixed(1)}%</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Tikimybė -10%</span>
                            <span class="stat-value" style="color: #ff4466;">${(info.prob_down_10_30d * 100).toFixed(1)}%</span>
                        </div>
                        
                        <div class="section-title">🔄 Mean Reversion</div>
                        <div class="stat-row">
                            <span class="stat-label">Vidutinė kaina</span>
                            <span class="stat-value">$${info.mean_reversion_price.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Nukrypimas (STD)</span>
                            <span class="stat-value">${info.deviation_std.toFixed(2)} σ</span>
                        </div>
                        
                        <div class="section-title">🌀 Fibonacci</div>
                        <div class="stat-row">
                            <span class="stat-label">Zona</span>
                            <span class="stat-value">${info.fibonacci_zone}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Bias</span>
                            <span class="stat-value">${info.fibonacci_bias}</span>
                        </div>
                        
                        <div class="section-title">📉 Volatility</div>
                        <div class="stat-row">
                            <span class="stat-label">Metinis</span>
                            <span class="stat-value">${(info.annual_volatility * 100).toFixed(1)}%</span>
                        </div>
                    `;
                    assetsDiv.appendChild(card);
                }
            } catch (error) {
                document.getElementById('loading').innerHTML = 
                    '<p style="color: #ff4466;">❌ Klaida: ' + error.message + '</p>';
            }
        }
        
        loadQuant();
        setInterval(loadQuant, 900000);  // 15 minutes
    </script>
</body>
</html>
"""

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# ================================
# MAIN
# ================================
def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    asyncio.run(signal_loop())

if __name__ == "__main__":
    main()
