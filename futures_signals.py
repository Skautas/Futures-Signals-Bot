import os
import sys
import asyncio
import platform
import tempfile
import atexit
import time
import signal
import ccxt
import pandas as pd
import numpy as np
import json
import urllib.request
from collections import deque, Counter
from datetime import datetime, timezone, timedelta
from typing import Tuple
import ta

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
_instance_lock_handle = None
entry_delay_state = {}

def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except PermissionError:
        return True
    except Exception:
        return False

def _terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
                ctypes.windll.kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        return False
    end_time = time.time() + timeout
    while time.time() < end_time:
        if not _pid_exists(pid):
            return True
        time.sleep(0.2)
    return not _pid_exists(pid)

def acquire_single_instance_lock() -> bool:
    """
    Ensure only one bot instance runs at a time.
    Uses a cross-platform file lock in temp directory.
    """
    global _instance_lock_handle
    lock_path = os.path.join(tempfile.gettempdir(), "futures_signals.lock")
    try:
        handle = open(lock_path, "a+")
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                try:
                    handle.seek(0)
                    existing_pid = handle.read().strip()
                    if existing_pid.isdigit() and not _pid_exists(int(existing_pid)):
                        handle.close()
                        os.remove(lock_path)
                        handle = open(lock_path, "a+")
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        if existing_pid.isdigit() and _terminate_pid(int(existing_pid)):
                            handle.close()
                            os.remove(lock_path)
                            handle = open(lock_path, "a+")
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            handle.close()
                            return False
                except Exception:
                    handle.close()
                    return False
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                try:
                    handle.seek(0)
                    existing_pid = handle.read().strip()
                    if existing_pid.isdigit() and not _pid_exists(int(existing_pid)):
                        handle.close()
                        os.remove(lock_path)
                        handle = open(lock_path, "a+")
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        if existing_pid.isdigit() and _terminate_pid(int(existing_pid)):
                            handle.close()
                            os.remove(lock_path)
                            handle = open(lock_path, "a+")
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        else:
                            handle.close()
                            return False
                except Exception:
                    handle.close()
                    return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _instance_lock_handle = handle

        def _release_lock():
            try:
                if _instance_lock_handle:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(_instance_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(_instance_lock_handle, fcntl.LOCK_UN)
                    _instance_lock_handle.close()
            except Exception:
                pass

        atexit.register(_release_lock)
        return True
    except Exception:
        return False

def sanitize_proxy_env():
    """Clear invalid local proxy settings that break HTTP calls."""
    bad_targets = ("127.0.0.1:9", "localhost:9")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key)
        if val and any(t in val for t in bad_targets):
            os.environ.pop(key, None)
            print(f"⚠️ Cleared invalid proxy env: {key}={val}")


def update_entry_delay_state(symbol, direction, trigger_candle, confirm_candle, required_confirms):
    state = entry_delay_state.get(symbol)
    if not state or state.get("direction") != direction:
        state = {"direction": direction, "trigger": trigger_candle, "confirmed": 0}
    result = evaluate_entry_delay(state["trigger"], confirm_candle, direction)
    if result.state == "CANCELLED":
        entry_delay_state.pop(symbol, None)
        return result
    if result.state == "CONFIRMED":
        state["confirmed"] += 1
        entry_delay_state[symbol] = state
        if state["confirmed"] >= required_confirms:
            return EntryDelayResult("CONFIRMED", f"Confirmed {state['confirmed']}x")
        return EntryDelayResult("WAITING_CONFIRMATION", f"Need {required_confirms}, have {state['confirmed']}")
    entry_delay_state[symbol] = state
    return EntryDelayResult("WAITING_CONFIRMATION", result.reason)
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from telegram import Bot
from flask import Flask, jsonify, render_template_string, render_template, send_from_directory, Response, request, redirect, url_for
import threading
import qrcode
import io
from dotenv import load_dotenv

from quant_analytics import QuantAnalytics, format_analysis_report
from ml_signals import ml_predictor
from sentiment_analyzer import sentiment_analyzer
from onchain_analytics import onchain_analytics
from trade_exit_engine import ExitContext, ExitLevels, calculate_exit_levels
from signal_density_engine import DensityContext, DensityResult, evaluate_signal_density
from async_safety_engine import AsyncResult, safe_call, guard_boolean, guard_numeric, safe_len
from market_regime_engine import RegimeContext as RegimeV2Context, RegimeResult, detect_market_regime as detect_regime_v2
from market_intel_engine import MarketIntelEngine
from net_profit_engine import (
    NetProfitDecision, 
    net_profit_engine,
    NetProfitContext,
    NetProfitResult,
    FeeConfig,
    optimize_rr_after_fees
)
from expectancy_engine import (
    ExpectancyContext,
    ExpectancyResult,
    evaluate_expectancy
)
from entry_optimizer_5m import optimize_entry_5m, EntryOptimizationResult
from entry_timing_filter import EntryTimingContext, entry_timing_filter
from pullback_entry_engine import PullbackContext, evaluate_pullback_entry, EntryState
from confluence_gate import ConfluenceContext, evaluate_confluence_gate, ConfluenceDecision
from impulse_exhaustion_filter import ImpulseContext, evaluate_impulse_exhaustion, ImpulseDecision
from structure.htf_structure import update_htf_structure, structure_hold, lower_low_printed, detect_swings
from filters.direction_lock import DirectionLock
from engine.mode_router import ModeRouter
from engine.cashflow_engine import process_signal as cashflow_process_signal
from engine.swing_engine import process_signal as swing_process_signal
from engine.location_engine import LocationEngine
from bot.market_state import MarketState, detect_market_state
from bot.location import Location, location_block
from bot.breakout import breakout_confirmed
from bot.indicators import indicator_score
from bot.decision_engine import evaluate_signal
from bot.pullback import evaluate_pullback
from bot.rejection import evaluate_rejection
from bot.fake_breakout import evaluate_fake_breakout
from bot.htf_sr import HTFContext, evaluate_htf_sr_gate
from bot.entry_delay import evaluate_entry_delay, EntryDelayResult
from bot.market_regime import RegimeContext as DecisionRegimeContext, detect_market_regime as detect_regime_ctx
from bot.holding_time import estimate_holding_time, HoldTimeContext, estimate_hold_time
from bot.zone_interaction import ZoneInteraction
from bot.zone_resolution import (
    ZoneResolutionEngine,
    Zone,
    ZoneType,
    Candle,
    ZoneResolutionState,
)
from bot.logger import log_decision
from zone_confidence import ZoneConfidenceContext, calculate_zone_confidence
from pro_strategies import (
    pro_analyzer, ProSignal, CandleReversal, BoxStrategy, BreakerBlock,
    ElliottWavePhase, FibonacciSweetSpot, ExhaustionGap, 
    CandleStrengthAnalyzer, AmateurHourFilter, CloseBasedSR,
    MoneyFlowIndex, ChaikinMoneyFlow, MoneyFlowDivergence, WaveScore, StopHuntDetector,
    MarketStructure, OrderBlocks, FairValueGap
)
from strategy_health_engine import StrategyHealthEngine
from entry_flow import MarketContext, SignalDecision, evaluate_entry, set_risk_event_logger, calculate_risk_modifier
from short_entry_flow import ShortMarketContext, evaluate_short_entry
from trading_hours import TradingHoursOptimizer
from asset_performance import AssetPerformance
from auto_adjust_targets import AutoAdjustTargets
from daily_report import DailyReport
from sunday_trading import SundayTradingEngine, SundayTradingConfig
from bear_market_mode import BearMarketConfig, BearMarketEngine
from config import (
    PROFIT_MODE_ENABLED as CONFIG_PROFIT_MODE_ENABLED,
    BEAR_MARKET,
    RISK,
    TRADING_HOURS_ENABLED,
    ZONE_RESOLUTION_MIN_BODY_PCT,
    ZONE_RESOLUTION_REQUIRED_CLOSES,
    RANGE_ALLOW_OUTSIDE_ZONE,
    ZONE_RESOLUTION_RELAXED_BODY_PCT,
    KRAKEN_OBSERVER_MODE,
    TRAILING_STOP_ON_EXCHANGE,
    TRAILING_MODEL,
)
try:
    from xgboost_trading import XGBoostTradingEngine, XGBoostConfig
    XGBOOST_AVAILABLE = True
    XGBOOST_IMPORT_ERROR = None
except Exception as e:
    XGBOOST_AVAILABLE = False
    XGBOOST_IMPORT_ERROR = str(e)
    XGBoostTradingEngine = None
    XGBoostConfig = None

# Profit Mode Integration
try:
    from profit_mode_simple import SimpleProfitTracker
    profit_tracker = SimpleProfitTracker()
    if CONFIG_PROFIT_MODE_ENABLED:
        print("✅ PROFIT MODE ACTIVATED")
except ImportError:
    profit_tracker = None

# ================================
# CONFIG
# ================================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
if CHAT_ID:
    CHAT_ID = CHAT_ID.strip()

# Strategy List
STRATEGY_LIST = [
    "TREND_CONTINUATION",
    "PULLBACK",
    "COUNTER_TREND",
    "SCALP_REBOUND",
    "BREAKOUT"
]

# Futures contracts (Perpetual) - 8 assets (BNB removed - no Kraken Perp)
FUTURES_ASSETS = [
    "PF_XBTUSD",   # BTC Perpetual
    "PF_ETHUSD",   # ETH Perpetual
    "PF_SOLUSD",   # SOL Perpetual
    "PF_XRPUSD",   # XRP Perpetual
    "PF_LTCUSD",   # LTC Perpetual
    "PF_ADAUSD",   # ADA Perpetual
    "PF_DOTUSD",   # DOT Perpetual
    "PF_LINKUSD",  # LINK Perpetual
]

ASSET_NAMES = {
    "PF_XBTUSD": "BTC",
    "PF_ETHUSD": "ETH",
    "PF_SOLUSD": "SOL",
    "PF_XRPUSD": "XRP",
    "PF_LTCUSD": "LTC",
    "PF_ADAUSD": "ADA",
    "PF_DOTUSD": "DOT",
    "PF_LINKUSD": "LINK",
}

# Timeframes
TIMEFRAME_MACRO = "4h"    # Macro analysis (big picture)
TIMEFRAME_TREND = "1h"    # Trend analysis
TIMEFRAME_ENTRY = "15m"   # Entry signals
TIMEFRAME_5M_OPTIMIZE = "5m"   # v8.9.24: Entry optimization only (no signal generation)
TIMEFRAME_DAILY = "1d"    # Daily liquidity zones (v8.5)
TIMEFRAME_WEEKLY = "1w"

# Signal settings
CHECK_INTERVAL = 30  # Day trading / cashflow mode – faster signal detection
MIN_SCORE = 55            # Minimum score for high-quality signals (was 0)
LEVERAGE = 5              # Kraken Futures fixed leverage
MIN_SIGNAL_CONFIDENCE = 0.60  # Minimum confidence (was 0.50)
SIGNAL_COOLDOWN_MINUTES = 30  # Minimum time between signals per asset
MAX_SIGNALS_PER_DAY = 5   # Global daily limit – max 5 signals per day

# Indicator settings
RSI_OVERSOLD = 30.0       # Cashflow mode: allow less extreme oversold
RSI_OVERBOUGHT = 69.50    # Overbought level (slightly below 70 for earlier entry)
ADX_MIN = 20
ADX_STRONG = 30

# Support/Resistance Breakout
BREAKOUT_LOOKBACK = 20    # Periods to find support/resistance
BREAKOUT_THRESHOLD = 0.002  # 0.2% breakout confirmation

# Trailing Stop Settings (overridden by TRAILING_MODEL in config.py per CASHFLOW/SWING)
TRAILING_ENABLED = True
TRAILING_DISTANCE_PCT = 1.5   # Fallback if TRAILING_MODEL not used
BREAKEVEN_AT_TP1 = True       # Fallback; TRAILING_MODEL.breakeven_at controls per mode

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
AUTO_TRADING_ENABLED = False     # Enable automatic position opening/closing
DISABLE_KRAKEN_IN_SIGNAL_ONLY = True  # Skip Kraken balance/positions when signal-only
AUTO_TRADE_MARGIN_USD = 50         # Initial margin in USD per trade (position = margin × leverage)
MAX_MARGIN_USD = 50                # Hard cap per-trade margin in USD
MAX_MARGIN_EUR = 50                # Hard cap per-trade margin in EUR (FX converted)
AUTO_TRADE_MAX_POSITIONS = RISK["MAX_POSITIONS"]       # Maximum concurrent open positions
AUTO_TRADE_MIN_SCORE = 55   # Same as MIN_SCORE – only high-quality for auto-trade
AUTO_CLOSE_ON_SL = True            # Automatically close position when SL is hit
AUTO_CLOSE_ON_TP3 = True           # Automatically close remaining position at TP3

# ================================
# PROFIT MODE CONFIGURATION
# ================================
PROFIT_MODE_ENABLED = False  # Disable cashflow gating to avoid mixing
DAILY_PROFIT_TARGET_EUR = 10  # 10€ per dieną
WEEKLY_PROFIT_TARGET_EUR = 60  # 60€ per savaitę
MIN_PROFIT_PER_TRADE_EUR = 1  # Minimalus pelnas vienam trade'ui
MAX_TRADES_PER_DAY = 10  # Maksimalus trade'ų skaičius per dieną
AUTO_ADJUST_TARGETS_ENABLED = True  # Auto-adjust daily target based on recent performance

# ================================
# POSITION CORRELATION CHECK
# ================================
POSITION_CORRELATION_ENABLED = True
POSITION_CORRELATION_THRESHOLD = 0.7
POSITION_CORRELATION_LOOKBACK = 80
POSITION_CORRELATION_MIN_POINTS = 20
POSITION_CORRELATION_TIMEFRAME = TIMEFRAME_TREND

# Signalų filtravimo pakeitimai
PROFIT_MODE_MIN_SCORE = 55  # Align with MIN_SCORE for quality
PROFIT_MODE_MIN_CONFIDENCE = 0.0
ACCEPT_PARTIAL_CONFLUENCE = True  # Priimti signalus su daline confluence

# Strategijų aktyvacija
ENABLE_SCALPING = False
ENABLE_DAY_TRADING = True
ENABLE_SWING_TRADING = True

# ================================
# ACCOUNT & RISK LIMITS (v8.9.18)
# ================================
DAILY_LOSS_LIMIT_PCT = RISK["DAILY_LOSS_LIMIT_PCT"]         # Max daily loss as % of capital (-2%)
WEEKLY_LOSS_LIMIT_PCT = RISK["WEEKLY_LOSS_LIMIT_PCT"]        # Max weekly loss as % of capital (-5%)
DAILY_LOSS_LIMIT_USD = 20          # Fallback: absolute $ limit if balance unavailable
MAX_DRAWDOWN_PCT = 10.0            # Maximum drawdown from peak equity (-10%) - v8.9.25

# ================================
# DYNAMIC LEVERAGE SETTINGS (v8.9)
# ================================
DYNAMIC_LEVERAGE_ENABLED = True   # Enable automatic leverage selection based on signal strength
MAX_RISK_PER_TRADE_USD = 4.0      # Maximum risk per trade in USD (fits 5 trades in $20 daily limit)

# ================================
# ENTRY EXECUTION TUNING (v8.9.28)
# ================================
LIMIT_ORDER_WAIT_SECONDS = 24          # Total wait for limit fill
LIMIT_CHASE_INTERVAL_SECONDS = 6       # How often to chase price
LIMIT_CHASE_MAX_STEPS = 3              # Number of chase attempts
LIMIT_CHASE_STEP_PCT = 0.06            # Marketable limit offset (%)
LIMIT_MAX_SLIPPAGE_PCT = 0.10          # Max allowed offset from initial price (%)
LIMIT_FALLBACK_TO_MARKET = True        # Fallback to market on timeout (high-quality only)
LIMIT_FALLBACK_MIN_SCORE = 55  # Align with MIN_SCORE for quality
ENTRY_OPTIMIZED_MAX_DEVIATION_PCT = 0.25  # Max optimized entry deviation (%)

# ================================
# FX RATE CACHE (EUR → USD)
# ================================
FX_RATE_CACHE_SECONDS = 3600
EUR_USD_FALLBACK_RATE = 1.04

# ================================
# LEVERAGE LIMITS
# ================================
MAX_LEVERAGE = 5

# Leverage tiers based on signal confidence
LEVERAGE_TIERS = {
    "STRONG": {
        "leverage": 5,
        "min_score": 85,
        "min_ml_confidence": 0.68,
        "min_confirmations": 5,
    },
    "MEDIUM": {
        "leverage": 4,
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
# HTTP SERVER
# ================================
ENABLE_HTTP_SERVER = True  # Flask dashboard at http://127.0.0.1:5000

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
QUANT_ENABLED = True                 # Matematinė analizė (Monte Carlo, ARIMA, Mean Reversion)
QUANT_COUNTER_TREND_ENABLED = False  # Disable quant-based counter-trend
QUANT_COUNTER_TREND_MIN_BIAS = 15    # Minimum quant score to allow counter-trend LONG (+15 or higher)
QUANT_DIRECTION_ENABLED = False      # Disable AI/Quant from direction decisions

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
# With 5x leverage: 0.15% price move = 0.75% account profit

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
    direction: str = "LONG"      # "LONG" | "SHORT"
    confluence_score: float = 0.0

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

def get_min_rr(direction: str, trend_strength: str, confluence_score: float) -> tuple:
    """
    Dynamic RR for DAY / CASHFLOW mode.
    Returns (min_rr, adjustments_explanation)
    """
    min_rr = 0.7
    reasons = ["base 0.7"]

    if trend_strength in ["STRONG_BULL", "STRONG_BEAR", "STRONG"]:
        min_rr = 0.6
        reasons.append("strong trend 0.6")

    if confluence_score < 50:
        min_rr = 1.0
        reasons.append("low confluence 1.0")

    return (round(min_rr, 2), " | ".join(reasons))

def rr_penalty(rr: float, min_rr: float) -> float:
    """Soft penalty system instead of hard blocking"""
    if rr >= min_rr:
        return 0.0
    diff = min_rr - rr
    return -float(int(diff * 20))

def is_scalp_mode(context: RRContext) -> bool:
    """Detects high-probability scalp environment"""
    return (
        context.trend_strength == "STRONG"
        and context.atr_ratio >= 1.4
        and context.score >= 65
        and not context.is_countertrend
    )

def evaluate_rr(context: RRContext) -> RRResult:
    """Main RR evaluation entry point - Dynamic RR + soft penalty"""
    min_rr, rr_adjustments = get_min_rr(
        context.direction,
        context.trend_strength,
        context.confluence_score
    )
    penalty = rr_penalty(context.rr, min_rr)
    scalp = is_scalp_mode(context)
    base_rr = 0.7

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
        reason="RR_OK" if penalty >= 0 else f"RR_SOFT ({context.rr:.2f} < {min_rr:.2f})",
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
MAX_CONSECUTIVE_LOSSES = 3    # Hard pause after 3 losses in a row
LOSS_COOLDOWN_ENABLED = True
LOSS_COOLDOWN_AFTER = 2       # Cooldown after 2 losses in a row
LOSS_COOLDOWN_MINUTES = 60    # Cooldown duration after 2 losses
LOSS_COOLDOWN_HARD_MINUTES = 180  # Cooldown duration after 3 losses
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
if KRAKEN_FUTURES_API_KEY:
    KRAKEN_FUTURES_API_KEY = KRAKEN_FUTURES_API_KEY.strip()
if KRAKEN_FUTURES_SECRET:
    KRAKEN_FUTURES_SECRET = KRAKEN_FUTURES_SECRET.strip()


# ================================
# API KEY VALIDATION (STARTUP CHECK)
# ================================
def validate_api_keys():
    """
    Validate that required API keys are present before starting the bot.
    Only validates if AUTO_TRADING_ENABLED is True (keys required for trading).
    Raises ValueError if keys are missing when trading is enabled.
    """
    # Only validate if auto-trading is enabled (keys required)
    if AUTO_TRADING_ENABLED:
        if not KRAKEN_FUTURES_API_KEY:
            raise ValueError("KRAKEN_FUTURES_API_KEY missing - required for auto-trading")
        
        if not KRAKEN_FUTURES_SECRET:
            raise ValueError("KRAKEN_FUTURES_SECRET missing - required for auto-trading")

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

fx_rate_cache = {
    "eur_usd": EUR_USD_FALLBACK_RATE,
    "last_update": None,
}

def get_eur_usd_rate() -> float:
    """Get EUR→USD FX rate with caching and fallback."""
    global fx_rate_cache
    
    now = datetime.now(timezone.utc)
    last_update = fx_rate_cache.get("last_update")
    if last_update and (now - last_update).total_seconds() < FX_RATE_CACHE_SECONDS:
        return fx_rate_cache.get("eur_usd", EUR_USD_FALLBACK_RATE)
    
    try:
        url = "https://api.exchangerate.host/latest?base=EUR&symbols=USD"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            rate = data.get("rates", {}).get("USD", None)
            if rate:
                fx_rate_cache["eur_usd"] = float(rate)
                fx_rate_cache["last_update"] = now
                return fx_rate_cache["eur_usd"]
    except Exception as e:
        print(f"⚠️ FX rate fetch error: {e}")
    
    fx_rate_cache["eur_usd"] = fx_rate_cache.get("eur_usd", EUR_USD_FALLBACK_RATE) or EUR_USD_FALLBACK_RATE
    fx_rate_cache["last_update"] = now
    return fx_rate_cache["eur_usd"]

def get_usd_eur_rate() -> float:
    """Get USD→EUR FX rate derived from EUR→USD."""
    eur_usd = get_eur_usd_rate()
    if eur_usd <= 0:
        return 1 / EUR_USD_FALLBACK_RATE
    return 1 / eur_usd

# ================================
# PROFIT TRACKER (EUR)
# ================================
class ProfitTracker:
    """Sekti pelno tikslus ir reguliuoti trading aktyvumą."""

    def __init__(self):
        self.daily_profit = 0.0
        self.weekly_profit = 0.0
        self.daily_trades = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.today_start = datetime.now(timezone.utc).date()
        self.week_start = self.get_week_start()
        self.aggression = 1.0

    def get_week_start(self):
        """Get Monday of current week (UTC)."""
        today = datetime.now(timezone.utc).date()
        return today - timedelta(days=today.weekday())

    def add_trade_result(self, profit_eur: float, position_size_eur: float = None):
        """Add trade result and update metrics."""
        self.daily_profit += profit_eur
        self.weekly_profit += profit_eur
        self.daily_trades += 1

        if profit_eur > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            if self.consecutive_wins >= 2:
                self.aggression = min(2.0, self.aggression * 1.2)
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            if self.consecutive_losses >= 2:
                self.aggression = max(0.5, self.aggression * 0.7)

        self.check_reset_daily()

    def check_reset_daily(self):
        """Reset daily counters if new day."""
        if datetime.now(timezone.utc).date() != self.today_start:
            if AUTO_ADJUST_TARGETS_ENABLED:
                auto_adjust_targets.update(self.daily_profit)
            self.daily_profit = 0.0
            self.daily_trades = 0
            self.today_start = datetime.now(timezone.utc).date()
            self.aggression = 1.0

        if datetime.now(timezone.utc).date() > self.week_start + timedelta(days=6):
            self.weekly_profit = 0.0
            self.week_start = self.get_week_start()

    def should_trade_more(self):
        """Check if we should continue trading today."""
        current_target = auto_adjust_targets.current_target if AUTO_ADJUST_TARGETS_ENABLED else DAILY_PROFIT_TARGET_EUR
        if self.daily_profit >= current_target:
            return False, "Daily profit target reached"

        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return False, "Daily trade limit reached"

        if self.daily_profit >= current_target * 0.8:
            return True, "Profit-taking mode (reduce risk)"

        return True, "Continue trading"

    def get_position_multiplier(self):
        """Get position size multiplier based on performance."""
        base_multiplier = 1.0
        hour = datetime.now(timezone.utc).hour
        if self.daily_profit < DAILY_PROFIT_TARGET_EUR * 0.5 and hour < 20:
            base_multiplier *= 1.3

        base_multiplier *= self.aggression

        if self.daily_profit >= DAILY_PROFIT_TARGET_EUR * 0.8:
            base_multiplier *= 0.7

        return min(base_multiplier, 2.0)


class QuickProfitStrategies:
    """Greito pelno strategijos."""

    @staticmethod
    def generate_scalp_signal(df_5m, asset):
        if df_5m is None or len(df_5m) < 50:
            return None

        rsi = RSIIndicator(df_5m['close'], window=14).rsi()
        current_rsi = rsi.iloc[-1]

        bb = BollingerBands(df_5m['close'], window=20, window_dev=2)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()

        current_price = df_5m['close'].iloc[-1]

        if current_price <= bb_lower.iloc[-1] * 1.01 and current_rsi < 35:
            return {
                'type': 'LONG',
                'strategy': 'SCALP_5M',
                'timeframe': '5m',
                'confidence': 0.65,
                'profit_target_pct': 0.5,
                'stop_loss_pct': 0.25,
                'hold_time_minutes': 15
            }

        if current_price >= bb_upper.iloc[-1] * 0.99 and current_rsi > 65:
            return {
                'type': 'SHORT',
                'strategy': 'SCALP_5M',
                'timeframe': '5m',
                'confidence': 0.65,
                'profit_target_pct': 0.5,
                'stop_loss_pct': 0.25,
                'hold_time_minutes': 15
            }

        return None

    @staticmethod
    def generate_swing_signal(df_1h, asset):
        if df_1h is None or len(df_1h) < 100:
            return None

        ema_12 = df_1h['close'].ewm(span=12).mean()
        ema_26 = df_1h['close'].ewm(span=26).mean()

        macd = MACD(df_1h['close'])
        macd_line = macd.macd()
        signal_line = macd.macd_signal()

        current_ema_12 = ema_12.iloc[-1]
        current_ema_26 = ema_26.iloc[-1]
        current_macd = macd_line.iloc[-1]
        current_signal = signal_line.iloc[-1]

        if current_ema_12 > current_ema_26 and current_macd > current_signal and current_macd > 0:
            support = df_1h['low'].rolling(20).min().iloc[-1]
            current_price = df_1h['close'].iloc[-1]
            risk_pct = (current_price - support) / current_price
            if risk_pct < 0.02:
                return {
                    'type': 'LONG',
                    'strategy': 'SWING_1H',
                    'timeframe': '1h',
                    'confidence': 0.70,
                    'profit_target_pct': 2.0,
                    'stop_loss_pct': risk_pct * 100,
                    'hold_time_hours': 24
                }

        return None


profit_tracker = ProfitTracker()
quick_strategies = QuickProfitStrategies()
asset_performance = AssetPerformance()
auto_adjust_targets = AutoAdjustTargets(DAILY_PROFIT_TARGET_EUR)
xgb_engine = None
sunday_engine = SundayTradingEngine(SundayTradingConfig())

def profit_mode_allows(signal):
    """Apply profit mode thresholds to a signal."""
    if not PROFIT_MODE_ENABLED:
        return True
    if signal.get('score', 0) < PROFIT_MODE_MIN_SCORE:
        return False
    if signal.get('confidence', 0) < PROFIT_MODE_MIN_CONFIDENCE:
        return False
    if not ACCEPT_PARTIAL_CONFLUENCE:
        if signal.get('confluence_score', 0) < 55:
            return False
    return True

def build_quick_signal(symbol, direction, base_price, strategy_name, confidence, profit_target_pct, stop_loss_pct):
    """Build a signal dict compatible with the bot."""
    sl_distance = base_price * (stop_loss_pct / 100)
    tp_distance = base_price * (profit_target_pct / 100)
    if direction == "LONG":
        sl = base_price - sl_distance
        tp1 = base_price + tp_distance
        tp2 = base_price + (tp_distance * 1.5)
        tp3 = base_price + (tp_distance * 2.5)
    else:
        sl = base_price + sl_distance
        tp1 = base_price - tp_distance
        tp2 = base_price - (tp_distance * 1.5)
        tp3 = base_price - (tp_distance * 2.5)

    return {
        "symbol": symbol,
        "direction": direction,
        "score": int(PROFIT_MODE_MIN_SCORE + 5),
        "base_score": PROFIT_MODE_MIN_SCORE,
        "signals": [strategy_name, "PROFIT_MODE"],
        "price": base_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "atr": 0,
        "rsi": 50.0,
        "trend": "NEUTRAL",
        "confidence": confidence,
        "modules_used": 1,
        "entry_type": "MARKET",
        "entry_reason": "💰 Profit mode quick signal",
        "strategy_name": "SCALP_REBOUND" if "SCALP" in strategy_name else "TREND_CONTINUATION",
        "time": datetime.now(timezone.utc)
    }

def build_xgb_market_data(df_15m, df_1h, df_4h, signal, current_price):
    market_data = {}
    try:
        if df_15m is not None and len(df_15m) >= 30:
            close_15m = df_15m["close"]
            rsi_14 = calc_rsi(close_15m, period=14).iloc[-1]
            rsi_28 = calc_rsi(close_15m, period=28).iloc[-1]
            market_data["rsi_14"] = float(rsi_14)
            market_data["rsi_28"] = float(rsi_28)

            macd = MACD(close_15m)
            market_data["macd"] = float(macd.macd().iloc[-1])
            market_data["macd_signal"] = float(macd.macd_signal().iloc[-1])
            market_data["macd_histogram"] = float(macd.macd_diff().iloc[-1])

            bb = BollingerBands(close_15m)
            bb_upper = bb.bollinger_hband().iloc[-1]
            bb_lower = bb.bollinger_lband().iloc[-1]
            bb_mid = bb.bollinger_mavg().iloc[-1]
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid else 0.1
            if current_price <= bb_lower:
                bb_position = -1
            elif current_price >= bb_upper:
                bb_position = 1
            else:
                bb_position = 0
            market_data["bb_width"] = float(bb_width)
            market_data["bb_position"] = float(bb_position)

            atr = calc_atr(df_15m).iloc[-1]
            atr_pct = (atr / current_price) if current_price > 0 else 0.02
            market_data["atr"] = float(atr)
            market_data["atr_pct"] = float(atr_pct)

            if "volume" in df_15m.columns and len(df_15m) >= 20:
                volume_ma = df_15m["volume"].rolling(20).mean().iloc[-1]
                current_volume = df_15m["volume"].iloc[-1]
                market_data["volume_ma_ratio"] = float(current_volume / volume_ma) if volume_ma else 1.0
                market_data["volume_vs_avg"] = market_data["volume_ma_ratio"]
        if df_1h is not None and len(df_1h) >= 50:
            close_1h = df_1h["close"]
            ema20 = calc_ema(close_1h, 20).iloc[-1]
            ema50 = calc_ema(close_1h, 50).iloc[-1]
            ema200 = calc_ema(close_1h, 200).iloc[-1] if len(close_1h) >= 200 else ema50
            market_data["ema_20"] = float(ema20)
            market_data["ema_50"] = float(ema50)
            market_data["ema_200"] = float(ema200)
            market_data["price_vs_ema20"] = (current_price - ema20) / ema20 if ema20 else 0
            market_data["price_vs_ema50"] = (current_price - ema50) / ema50 if ema50 else 0
            market_data["price_vs_ema200"] = (current_price - ema200) / ema200 if ema200 else 0

            recent_low = df_1h["low"].rolling(20).min().iloc[-1]
            recent_high = df_1h["high"].rolling(20).max().iloc[-1]
            market_data["dist_to_support_pct"] = (current_price - recent_low) / current_price if current_price else 0.05
            market_data["dist_to_resistance_pct"] = (recent_high - current_price) / current_price if current_price else 0.05
    except Exception:
        pass

    last_candle = df_15m.iloc[-1] if df_15m is not None and len(df_15m) > 0 else None
    if last_candle is not None and current_price > 0:
        candle_body = abs(last_candle["close"] - last_candle["open"])
        candle_range = max(1e-9, last_candle["high"] - last_candle["low"])
        market_data["candle_size_pct"] = float(candle_body / current_price * 100)
        market_data["candle_body_ratio"] = float(candle_body / candle_range)
        market_data["wick_ratio"] = float((candle_range - candle_body) / candle_range)

    market_data["market_regime"] = market_regime_state.get("regime", "NEUTRAL")
    market_data["corr_btc"] = 0
    signal["asset"] = ASSET_NAMES.get(signal.get("symbol", ""), signal.get("symbol", "BTC"))
    signal["strategy"] = signal.get("strategy_name", "TREND_CONTINUATION")
    return market_data

def _returns_from_df(df):
    if df is None or "close" not in df:
        return None
    closes = df["close"].astype(float)
    returns = closes.pct_change().dropna()
    if len(returns) < POSITION_CORRELATION_MIN_POINTS:
        return None
    return returns

async def check_position_correlation(symbol, direction, df_reference=None):
    """Block signal if highly correlated with an existing position in same direction."""
    if not POSITION_CORRELATION_ENABLED:
        return False, None
    if not open_positions:
        return False, None

    current_returns = _returns_from_df(df_reference)
    if current_returns is None:
        df_ref = await fetch_ohlcv(symbol, POSITION_CORRELATION_TIMEFRAME, POSITION_CORRELATION_LOOKBACK)
        current_returns = _returns_from_df(df_ref)
    if current_returns is None:
        return False, None

    for open_symbol, pos in open_positions.items():
        if open_symbol == symbol:
            continue
        if pos.get("direction") != direction:
            continue

        df_other = await fetch_ohlcv(open_symbol, POSITION_CORRELATION_TIMEFRAME, POSITION_CORRELATION_LOOKBACK)
        other_returns = _returns_from_df(df_other)
        if other_returns is None:
            continue

        min_len = min(len(current_returns), len(other_returns))
        corr = current_returns.tail(min_len).corr(other_returns.tail(min_len))
        if corr is None or pd.isna(corr):
            continue

        if abs(corr) >= POSITION_CORRELATION_THRESHOLD:
            return True, f"{open_symbol} corr={corr:.2f}"

    return False, None

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
    
    if DISABLE_KRAKEN_IN_SIGNAL_ONLY and not AUTO_TRADING_ENABLED and not KRAKEN_OBSERVER_MODE:
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
                        eur_usd_rate = get_eur_usd_rate()
                        eur_usd = total * eur_usd_rate
                        flex_usd += eur_usd
                        collaterals[currency]['usd_value'] = eur_usd
        
        total_usd = cash_usd + flex_usd
        usd_eur_rate = get_usd_eur_rate()
        total_eur = total_usd * usd_eur_rate
        
        account_balance_cache = {
            "total_usd": round(total_usd, 2),
            "total_eur": round(total_eur, 2),
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
daily_signal_count = 0
daily_signal_reset_date = None  # UTC date, resets at midnight
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

telegram_stats = {
    "last_send": None,
    "last_method": None,
    "last_message_id": None,
    "last_error": None,
    "last_error_type": None,
    "last_chat_id_masked": None,
}

# ================================
# ADAPTIVE FILTERING (signal flow)
# ================================
ADAPTIVE_ENABLED = True
ADAPTIVE_SIGNAL_WINDOW_MIN = 180  # minutes for signal-rate tracking
ADAPTIVE_LOG_INTERVAL_MIN = 30
BOUNCE_LONG_ENABLED = True
BOUNCE_LONG_MIN_CONFLUENCE = 45

# Auto-tune filters based on live block stats
AUTO_TUNE_ENABLED = True
AUTO_TUNE_APPLY_INTERVAL_MIN = 10
AUTO_TUNE_SIGNAL_WINDOW_MIN = 90
AUTO_TUNE_MIN_BLOCKS = 1
AUTO_TUNE_MAX_LEVEL = 3
AUTO_TUNE_TARGETS = [
    "PULLBACK_ENGINE",
    "IMPULSE_FILTER",
    "RSI_1H_PREFILTER",
    "CONSOLIDATION",
    "CONFLUENCE_GATE",
    "ORDER_FLOW",
    "ENTRY_TIMING",
]

# EMA distance override (strong bear only)
EMA_DISTANCE_OVERRIDE_ENABLED = True
EMA_DISTANCE_OVERRIDE_MIN_SCORE = 40

# Trade mode: CASHFLOW or SWING
TRADE_MODE = "CASHFLOW"


def tp_multiplier_by_zone(zone_confidence, trade_mode):
    """
    CASHFLOW: scale TP by zone confidence.
    >= 70: 1.0 | 50-69: 0.65 | 35-49: 0.45 | < 35: 0.30
    SWING: no scaling.
    """
    if trade_mode != "CASHFLOW":
        return 1.0
    zc = zone_confidence or 0
    if zc >= 70:
        return 1.0
    if zc >= 50:
        return 0.65
    if zc >= 35:
        return 0.45
    return 0.30


ZONE_IMPULSE_MULT = 1.5
ZONE_BUFFER_ATR = 0.2

adaptive_state = {
    "recent_signals": deque(),
    "last_signal_time": None,
    "last_log_time": None,
    "last_relax_level": 0,
    "blocked_counts": Counter(),
    "last_block_reset": None,
}

auto_tune_state = {
    "levels": {
        "PULLBACK_ENGINE": 0,
        "IMPULSE_FILTER": 0,
        "RSI_1H_PREFILTER": 0,
        "CONSOLIDATION": 0,
        "CONFLUENCE_GATE": 0,
        "ORDER_FLOW": 0,
        "ENTRY_TIMING": 0,
    },
    "last_apply": None,
}

def _prune_recent_signals(now: datetime, window_min: int = ADAPTIVE_SIGNAL_WINDOW_MIN):
    window = timedelta(minutes=window_min)
    dq = adaptive_state["recent_signals"]
    while dq and (now - dq[0]) > window:
        dq.popleft()

def record_signal_sent():
    if not ADAPTIVE_ENABLED:
        return
    now = datetime.now(timezone.utc)
    adaptive_state["recent_signals"].append(now)
    adaptive_state["last_signal_time"] = now
    _prune_recent_signals(now)

def get_recent_signal_count(window_min: int = AUTO_TUNE_SIGNAL_WINDOW_MIN) -> int:
    now = datetime.now(timezone.utc)
    _prune_recent_signals(now, window_min=window_min)
    return len(adaptive_state["recent_signals"])

def get_auto_tune_level(filter_name: str) -> int:
    return int(auto_tune_state["levels"].get(filter_name, 0))

def get_effective_relax_level(filter_name: str) -> int:
    base = get_adaptive_relax_level()
    extra = get_auto_tune_level(filter_name)
    return min(5, base + extra)

def maybe_apply_auto_tune():
    if not AUTO_TUNE_ENABLED:
        return
    now = datetime.now(timezone.utc)
    last_apply = auto_tune_state.get("last_apply")
    if last_apply and (now - last_apply).total_seconds() < AUTO_TUNE_APPLY_INTERVAL_MIN * 60:
        return
    recent_signals = get_recent_signal_count()
    blocks = adaptive_state["blocked_counts"]
    tuned = []
    decayed = []
    if recent_signals == 0:
        for filter_name in AUTO_TUNE_TARGETS:
            if blocks.get(filter_name, 0) >= AUTO_TUNE_MIN_BLOCKS:
                current = get_auto_tune_level(filter_name)
                if current < AUTO_TUNE_MAX_LEVEL:
                    auto_tune_state["levels"][filter_name] = current + 1
                    tuned.append(f"{filter_name}+{current + 1}")
    elif recent_signals >= 2:
        for filter_name, current in auto_tune_state["levels"].items():
            if current > 0:
                auto_tune_state["levels"][filter_name] = current - 1
                decayed.append(f"{filter_name}-{current - 1}")
    if tuned or decayed:
        blocks_str = ", ".join([f"{k}:{v}" for k, v in blocks.items()]) if blocks else "none"
        print(f"🧰 AutoTune: {', '.join(tuned + decayed)} | blocks={blocks_str} | signals_last_{AUTO_TUNE_SIGNAL_WINDOW_MIN}m={recent_signals}")
        auto_tune_state["last_apply"] = now

def record_block(reason: str):
    if not ADAPTIVE_ENABLED:
        return
    if not reason:
        return
    adaptive_state["blocked_counts"][reason] += 1

def get_adaptive_relax_level() -> int:
    if not ADAPTIVE_ENABLED:
        return 0
    now = datetime.now(timezone.utc)
    last_signal = adaptive_state.get("last_signal_time")
    silence_sec = (now - last_signal).total_seconds() if last_signal else None
    level = 0
    if silence_sec is None or silence_sec >= 3 * 3600:
        level = 3
    elif silence_sec >= 90 * 60:
        level = 2
    elif silence_sec >= 45 * 60:
        level = 1
    # If flow is healthy, ease back
    _prune_recent_signals(now, window_min=90)
    recent_2h = len(adaptive_state["recent_signals"])
    if recent_2h >= 3:
        level = max(0, level - 1)
    adaptive_state["last_relax_level"] = level
    return level

def get_adaptive_rsi_prefilter_thresholds() -> tuple:
    level = get_effective_relax_level("RSI_1H_PREFILTER")
    long_min = min(60, 40 + (level * 7))
    short_max = max(40, 60 - (level * 7))
    return long_min, short_max

def get_adaptive_consolidation_thresholds() -> tuple:
    level = get_effective_relax_level("CONSOLIDATION")
    atr_thr = max(0.20, CONSOLIDATION_ATR_THRESHOLD - (0.05 * level))
    range_edge = min(60, CONSOLIDATION_RANGE_EDGE_PCT + (5 * level))
    momentum_min = max(15, CONSOLIDATION_MOMENTUM_MIN - (3 * level))
    adx_min = max(10, CONSOLIDATION_ADX_MIN - (2 * level))
    return atr_thr, range_edge, momentum_min, adx_min

def adaptive_impulse_bypass_threshold() -> int:
    level = get_adaptive_relax_level()
    if level >= 3:
        return 50
    if level == 2:
        return 60
    if level == 1:
        return 70
    return 80

def maybe_log_adaptive_status():
    if not ADAPTIVE_ENABLED:
        return
    now = datetime.now(timezone.utc)
    last_log = adaptive_state.get("last_log_time")
    if last_log and (now - last_log).total_seconds() < ADAPTIVE_LOG_INTERVAL_MIN * 60:
        return
    relax_level = get_adaptive_relax_level()
    top_blocks = adaptive_state["blocked_counts"].most_common(3)
    blocks_str = ", ".join([f"{k}:{v}" for k, v in top_blocks]) if top_blocks else "none"
    print(f"🧠 Adaptive status: relax={relax_level} | blocks={blocks_str}")
    adaptive_state["last_log_time"] = now
    maybe_apply_auto_tune()
    adaptive_state["blocked_counts"].clear()

# ================================
# MARKET INTEL (structure analytics)
# ================================
MARKET_INTEL_REFRESH_INTERVAL = 1800  # seconds
market_intel_engine = MarketIntelEngine()
market_intel_results = {}
market_intel_last_update = None

def _load_market_intel_cache():
    cache_path = os.path.join(os.path.dirname(__file__), "market_intel_cache.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Market intel cache load error: {e}")
        return None

def _save_market_intel_cache(results, last_update):
    cache_path = os.path.join(os.path.dirname(__file__), "market_intel_cache.json")
    try:
        payload = {
            "assets": results,
            "last_update": last_update.isoformat() if last_update else None
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        print(f"Market intel cache save error: {e}")

def run_market_intel_sync():
    results = {}
    for symbol in FUTURES_ASSETS:
        df_1h = fetch_ohlcv_sync(symbol, TIMEFRAME_TREND, limit=120)
        df_4h = fetch_ohlcv_sync(symbol, TIMEFRAME_MACRO, limit=120)
        if df_1h is None or df_4h is None:
            continue
        analysis = market_intel_engine.analyze_asset(df_1h, df_4h)
        results[symbol] = analysis
    return results

def mask_chat_id(chat_id: str) -> str:
    if not chat_id:
        return None
    cid = str(chat_id)
    if len(cid) <= 4:
        return "*" * len(cid)
    return f"{cid[:2]}***{cid[-2:]}"

HEARTBEAT_INTERVAL = 7200  # 2 hours in seconds

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
        sent = await tg_bot.send_message(chat_id=CHAT_ID, text=message)
        telegram_stats["last_send"] = now.isoformat()
        telegram_stats["last_method"] = "heartbeat"
        telegram_stats["last_message_id"] = getattr(sent, "message_id", None)
        telegram_stats["last_error"] = None
        telegram_stats["last_error_type"] = None
        telegram_stats["last_chat_id_masked"] = mask_chat_id(CHAT_ID)
        bot_stats["last_heartbeat"] = now
        print(f"💚 Heartbeat sent at {now.strftime('%H:%M:%S')} UTC")
    except Exception as e:
        print(f"⚠️ Heartbeat send error: {e}")
        telegram_stats["last_send"] = now.isoformat()
        telegram_stats["last_method"] = "heartbeat"
        telegram_stats["last_message_id"] = None
        telegram_stats["last_error"] = str(e)
        telegram_stats["last_error_type"] = type(e).__name__
        telegram_stats["last_chat_id_masked"] = mask_chat_id(CHAT_ID)
        bot_stats["last_heartbeat"] = now  # Still update to prevent spam

# ================================
# WIN/LOSS TRACKING SYSTEM
# ================================
SIGNALS_FILE = "signal_results.json"
STATE_FILE = "bot_state.json"  # State persistence file

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

def save_bot_state():
    """Išsaugoti bot state (open_positions, auto_trading_state) į failą"""
    global open_positions, auto_trading_state
    try:
        state = {
            "open_positions": open_positions,
            "auto_trading_state": auto_trading_state,
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        # Convert datetime objects to strings
        def convert_datetime(obj):
            if isinstance(obj, dict):
                return {k: convert_datetime(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_datetime(item) for item in obj]
            elif isinstance(obj, datetime):
                return obj.isoformat()
            return obj
        
        state = convert_datetime(state)
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
        return True
    except Exception as e:
        print(f"⚠️ Error saving bot state: {e}")
        return False

def load_bot_state():
    """Įkelti bot state iš failo"""
    global open_positions, auto_trading_state
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            
            # Restore auto_trading_state
            if 'auto_trading_state' in state:
                saved_state = state['auto_trading_state']
                for key in auto_trading_state:
                    if key in saved_state:
                        auto_trading_state[key] = saved_state[key]
            
            # Restore open_positions (convert datetime strings back)
            if 'open_positions' in state:
                saved_positions = state['open_positions']
                for symbol, pos_data in saved_positions.items():
                    # Convert datetime strings back to datetime objects
                    for key, value in pos_data.items():
                        if isinstance(value, str) and ('time' in key.lower() or 'date' in key.lower()):
                            try:
                                pos_data[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                            except:
                                pass
                    open_positions[symbol] = pos_data
            
            saved_at = state.get('saved_at', 'unknown')
            print(f"✅ Loaded bot state from {saved_at}")
            print(f"   - Open positions: {len(open_positions)}")
            print(f"   - Daily P&L: ${auto_trading_state.get('daily_pnl', 0):.2f}")
            print(f"   - Weekly P&L: ${auto_trading_state.get('weekly_pnl', 0):.2f}")
            return True
    except Exception as e:
        print(f"⚠️ Error loading bot state: {e}")
    return False

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

def get_recent_trades(strategy_name, limit=20):
    """
    Get recent completed trades for a specific strategy (rolling statistics)
    
    Args:
        strategy_name: Strategy name from STRATEGY_LIST
        limit: Maximum number of recent trades to return (default: 20)
    
    Returns:
        List of recent trades (most recent last)
    """
    data = load_signal_results()
    trades = [
        s for s in data["signals"]
        if s.get("strategy") == strategy_name and s.get("result") in ("WIN", "LOSS")
    ]
    return trades[-limit:]

def calculate_metrics(trades):
    """
    Calculate trading metrics from a list of trades
    
    Args:
        trades: List of trade dictionaries with "result" and "profit_pct" fields
    
    Returns:
        Tuple of (win_rate, expectancy, consecutive_losses) or None if no trades
    """
    if not trades:
        return None

    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = len(trades) - wins
    win_rate = wins / len(trades)

    avg_profit = sum(t["profit_pct"] for t in trades) / len(trades)
    avg_loss = abs(sum(t["profit_pct"] for t in trades if t["profit_pct"] < 0) / max(1, losses))

    rr = avg_profit / avg_loss if avg_loss > 0 else 1.0
    expectancy = win_rate * rr - (1 - win_rate)

    # consecutive losses
    cons_losses = 0
    for t in reversed(trades):
        if t["result"] == "LOSS":
            cons_losses += 1
        else:
            break

    return win_rate, expectancy, cons_losses

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
        "is_counter_trend": is_counter_trend,  # v8.9.4: Track CT signals
        "strategy": signal.get("strategy_name", "TREND_CONTINUATION")  # Strategy identification
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

quant_engine = QuantAnalytics() if QUANT_ENABLED else None
quant_results = {}
quant_correlation = None
quant_last_update = None

# Strategy Health Engine
strategy_health_engine = StrategyHealthEngine()

# Bear Market Engine
bear_config = BearMarketConfig(
    ENABLED=True,
    AUTO_DETECT=not BEAR_MARKET["OVERRIDE_AUTO_DETECT"]
)
bear_engine = BearMarketEngine(bear_config)
if BEAR_MARKET["MANUAL_OVERRIDE_LOCK"]:
    bear_engine.detector.set_manual_override(True, BEAR_MARKET["STRENGTH"])
if BEAR_MARKET["FORCE_ACTIVATE"]:
    bear_engine.detector.set_manual_override(True, BEAR_MARKET["STRENGTH"])
    print("🔥 BEAR MARKET MANUALLY OVERRIDDEN")
bear_last_update = None
BEAR_MARKET_UPDATE_INTERVAL = 6 * 3600  # seconds

# HTF structure lock
direction_lock = DirectionLock()
mode_router = ModeRouter()
mode_router.register("CASHFLOW", cashflow_process_signal)
mode_router.register("SWING", swing_process_signal)
htf_last_update = None
htf_last_candle_time = None
htf_state = {"bos": None, "lock": None, "choch": None}

# Position tracking for trailing stop
open_positions = {}  # symbol -> position_data
position_lock = asyncio.Lock()  # Lock for thread-safe position operations

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
    "cooldown_until": None,
}

# Risk Events Log (v8.9.18)
risk_events_log = []  # List of risk events for analytics
MAX_RISK_EVENTS = 100  # Keep last 100 events

# Critical Alert Deduplication (prevent spam)
critical_alerts_sent = {}  # event_type -> last_sent_timestamp
CRITICAL_ALERT_COOLDOWN = RISK["CRITICAL_ALERT_COOLDOWN"]  # 1 hour cooldown between same alert type

# Entry Timing State Tracking (WAIT → ARM → ENTER)
entry_timing_states = {}  # symbol -> {"state": "WAIT"/"ARM"/"ENTER", "distance_pct": float, "last_check": datetime}
zone_wait_status = {}  # symbol -> {"state": str, "last_sent": datetime}
zone_close_states = {}  # key -> {"state": ZoneInteraction, "break_candle": dict, "break_time": datetime}
ZONE_WAIT_STATUS_COOLDOWN_MIN = 30
pullback_impulse_states = {}  # symbol -> "NO_IMPULSE"/"HOT"/"COOLING"


async def send_telegram_critical_alert(event_type: str, details: str, event_data: dict = None):
    """
    Send critical alert to Telegram with deduplication.
    Prevents spam by only sending same alert type once per cooldown period.
    """
    global critical_alerts_sent
    
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    # Check cooldown
    now = datetime.now(timezone.utc)
    last_sent = critical_alerts_sent.get(event_type)
    
    if last_sent:
        time_since_last = (now - last_sent).total_seconds()
        if time_since_last < CRITICAL_ALERT_COOLDOWN:
            # Still in cooldown, skip
            return
    
    try:
        # Build alert message
        emoji_map = {
            "BALANCE_FETCH_FAILED": "🚫",
            "API_TIMEOUT": "⏱️",
            "DAILY_LIMIT": "🔴",
            "WEEKLY_LIMIT": "🔴",
            "FUND_FLOW_ERROR": "⚠️",
            "API_KEY_ERROR": "🔑",
            "MAX_DRAWDOWN": "📉",
        }
        
        emoji = emoji_map.get(event_type, "⚠️")
        
        # Get additional context
        balance = get_available_balance()
        capital = balance.get("total_usd", 0)
        daily_pnl = auto_trading_state.get("daily_pnl", 0)
        weekly_pnl = auto_trading_state.get("weekly_pnl", 0)
        open_pos_count = len([p for p in open_positions.values() if p])
        
        message = f"""{emoji} <b>CRITICAL ALERT</b> {emoji}

🔴 <b>{event_type}</b>

📝 <b>Details:</b>
{details}

💰 <b>Current Status:</b>
Capital: ${capital:.2f}
Daily P&L: ${daily_pnl:+.2f}
Weekly P&L: ${weekly_pnl:+.2f}
Open Positions: {open_pos_count}

⏰ {now.strftime('%Y-%m-%d %H:%M:%S UTC')}

<i>This is a critical system alert. Please review immediately.</i>"""
        
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        
        # Update last sent timestamp
        critical_alerts_sent[event_type] = now
        
        print(f"📱 Telegram critical alert sent: {event_type}")
        
    except Exception as e:
        print(f"⚠️ Failed to send Telegram critical alert: {e}")


def log_risk_event(event_type, details, severity="warning"):
    """
    Log a risk event for analytics.
    event_type: "DAILY_LIMIT", "WEEKLY_LIMIT", "CONSECUTIVE_LOSSES", "VOLATILITY_SPIKE", "EMERGENCY_STOP"
    severity: "info", "warning", "critical"
    """
    global risk_events_log
    try:
        balance_snapshot = get_available_balance()
        capital_at_event = balance_snapshot.get("total_usd", 0)
    except Exception:
        capital_at_event = 0
    
    event = {
        "type": event_type,
        "details": details,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_at_event": capital_at_event,
        "daily_pnl": auto_trading_state.get("daily_pnl", 0),
        "weekly_pnl": auto_trading_state.get("weekly_pnl", 0),
        "consecutive_losses": circuit_state.get("consecutive_losses", 0),
    }
    risk_events_log.append(event)
    
    # Trim to max size
    if len(risk_events_log) > MAX_RISK_EVENTS:
        risk_events_log = risk_events_log[-MAX_RISK_EVENTS:]
    
    print(f"⚠️ RISK EVENT: {event_type} - {details} (severity: {severity})")
    
    # Send Telegram alert for critical events
    if severity == "critical":
        # Schedule async Telegram alert (non-blocking)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Event loop is running - create task
                asyncio.create_task(send_telegram_critical_alert(event_type, details, event))
            else:
                # No event loop running - will be sent when loop starts
                pass  # Will be handled when async context is available
        except RuntimeError:
            # No event loop available - will be sent when async context is available
            pass


async def send_telegram_critical_alert(event_type: str, details: str, event_data: dict = None):
    """
    Send critical alert to Telegram with deduplication.
    Prevents spam by only sending same alert type once per cooldown period.
    """
    global critical_alerts_sent
    
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    # Check cooldown
    now = datetime.now(timezone.utc)
    last_sent = critical_alerts_sent.get(event_type)
    
    if last_sent:
        time_since_last = (now - last_sent).total_seconds()
        if time_since_last < CRITICAL_ALERT_COOLDOWN:
            # Still in cooldown, skip
            return
    
    try:
        # Build alert message
        emoji_map = {
            "BALANCE_FETCH_FAILED": "🚫",
            "API_TIMEOUT": "⏱️",
            "DAILY_LIMIT": "🔴",
            "WEEKLY_LIMIT": "🔴",
            "FUND_FLOW_ERROR": "⚠️",
            "API_KEY_ERROR": "🔑",
            "MAX_DRAWDOWN": "📉",
        }
        
        emoji = emoji_map.get(event_type, "⚠️")
        
        # Get additional context
        balance = get_available_balance()
        capital = balance.get("total_usd", 0)
        daily_pnl = auto_trading_state.get("daily_pnl", 0)
        weekly_pnl = auto_trading_state.get("weekly_pnl", 0)
        open_pos_count = len([p for p in open_positions.values() if p])
        
        message = f"""{emoji} <b>CRITICAL ALERT</b> {emoji}

🔴 <b>{event_type}</b>

📝 <b>Details:</b>
{details}

💰 <b>Current Status:</b>
Capital: ${capital:.2f}
Daily P&L: ${daily_pnl:+.2f}
Weekly P&L: ${weekly_pnl:+.2f}
Open Positions: {open_pos_count}

⏰ {now.strftime('%Y-%m-%d %H:%M:%S UTC')}

<i>This is a critical system alert. Please review immediately.</i>"""
        
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        
        # Update last sent timestamp
        critical_alerts_sent[event_type] = now
        
        print(f"📱 Telegram critical alert sent: {event_type}")
        
    except Exception as e:
        print(f"⚠️ Failed to send Telegram critical alert: {e}")

# FOMC State
fomc_alert_sent = set()

# Kraken Live Positions State
kraken_positions = {
    "positions": {},  # symbol -> {"direction": "LONG"/"SHORT", "size": float}
    "last_fetch": None,
    "fetch_interval": 30,  # seconds between API calls
    "fetch_failed": False,
    "consecutive_failures": 0,
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
    "pause_type": None,         # "DAILY", "WEEKLY", or "MAX_DRAWDOWN"
    "peak_equity": 0.0,         # Highest equity ever reached (v8.9.25 - Max Drawdown)
    "peak_equity_date": None,   # Date when peak equity was reached
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
    
    eur_usd_rate = get_eur_usd_rate()
    max_margin_usd_from_eur = MAX_MARGIN_EUR * eur_usd_rate
    effective_margin_usd = min(AUTO_TRADE_MARGIN_USD, MAX_MARGIN_USD, max_margin_usd_from_eur)
    
    # Calculate position size based on tier leverage and margin
    tier_position_size = effective_margin_usd * tier_leverage
    
    # Use SMALLER of the two (risk-capped)
    if max_position_by_risk < tier_position_size:
        position_size_usd = max_position_by_risk
        risk_adjusted = True
        # Recalculate effective leverage
        selected_leverage = max(1, min(MAX_LEVERAGE, int(position_size_usd / effective_margin_usd)))
        tier_reason.append(f"🛡️ Risk-sized: ${position_size_usd:.0f} (SL {effective_sl_pct*100:.1f}%)")
    else:
        position_size_usd = tier_position_size
        selected_leverage = tier_leverage
    
    # Calculate actual margin (collateral) needed
    margin_usd = position_size_usd / selected_leverage if selected_leverage > 0 else effective_margin_usd
    
    # Hard caps for safety
    # Max position: 5x leverage on EUR-capped margin
    # Min position: $10 (too small = high fee impact)
    MAX_POSITION_USD = 250
    MIN_POSITION_USD = 10
    max_position_by_eur = max_margin_usd_from_eur * MAX_LEVERAGE
    max_position_by_usd = MAX_MARGIN_USD * MAX_LEVERAGE
    hard_max_position_usd = min(MAX_POSITION_USD, max_position_by_eur, max_position_by_usd)
    
    if position_size_usd > hard_max_position_usd:
        position_size_usd = hard_max_position_usd
        tier_reason.append(f"Capped at ${hard_max_position_usd:.0f} (EUR cap)")
    
    if position_size_usd < MIN_POSITION_USD:
        position_size_usd = MIN_POSITION_USD
        tier_reason.append(f"Min size ${MIN_POSITION_USD}")
    
    # Recalculate leverage based on final position
    selected_leverage = max(1, min(MAX_LEVERAGE, int(position_size_usd / effective_margin_usd)))
    margin_usd = min(effective_margin_usd, position_size_usd / selected_leverage) if selected_leverage > 0 else effective_margin_usd
    
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
# SAFE EXCHANGE API WRAPPER (with timeout)
# ================================
async def safe_exchange_call(func, *args, timeout=10, max_retries=3, **kwargs):
    """
    Wrapper for exchange API calls with timeout and retry logic.
    Prevents bot from hanging indefinitely on API failures.
    """
    for attempt in range(max_retries):
        try:
            # Use asyncio.wait_for for timeout
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            else:
                # Synchronous function - run in executor
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                    timeout=timeout
                )
            return result
        except asyncio.TimeoutError:
            # Only log as critical on final attempt (after all retries failed)
            if attempt == max_retries - 1:
                log_risk_event("API_TIMEOUT", f"{func.__name__} timed out after {max_retries} attempts - operation failed", "critical")
                raise
            else:
                # Log as warning for intermediate timeouts
                log_risk_event("API_TIMEOUT", f"{func.__name__} timed out (attempt {attempt+1}/{max_retries}) - retrying...", "warning")
            await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    return None

# ================================
# MAXIMUM DRAWDOWN PROTECTION (v8.9.25)
# ================================
def update_peak_equity(current_equity: float):
    """
    Update peak equity if current equity exceeds previous peak.
    Peak equity is the highest equity ever reached (used for drawdown calculation).
    """
    global auto_trading_state
    
    if current_equity <= 0:
        return
    
    peak = auto_trading_state.get("peak_equity", 0.0)
    
    # Update peak if current equity is higher
    if current_equity > peak:
        auto_trading_state["peak_equity"] = current_equity
        auto_trading_state["peak_equity_date"] = datetime.now(timezone.utc).isoformat()
        print(f"📈 New peak equity: ${current_equity:.2f}")

def check_max_drawdown(current_equity: float) -> Tuple[bool, str, float]:
    """
    Check if maximum drawdown limit is reached.
    
    Args:
        current_equity: Current total equity (capital + unrealized P&L)
    
    Returns:
        tuple: (is_paused, reason, drawdown_pct)
    """
    global auto_trading_state
    
    peak = auto_trading_state.get("peak_equity", 0.0)
    
    # If no peak set yet, set current equity as peak
    if peak == 0.0 and current_equity > 0:
        auto_trading_state["peak_equity"] = current_equity
        auto_trading_state["peak_equity_date"] = datetime.now(timezone.utc).isoformat()
        return False, "", 0.0
    
    if peak <= 0:
        return False, "", 0.0
    
    # Calculate drawdown from peak
    drawdown_usd = peak - current_equity
    drawdown_pct = (drawdown_usd / peak) * 100 if peak > 0 else 0.0
    
    # Check if drawdown limit reached
    if drawdown_pct >= MAX_DRAWDOWN_PCT:
        return True, f"Max drawdown -{MAX_DRAWDOWN_PCT:.1f}% reached (from ${peak:.2f} to ${current_equity:.2f}, -{drawdown_pct:.2f}%)", drawdown_pct
    
    return False, "", drawdown_pct

def get_current_equity() -> float:
    """
    Calculate current total equity (capital + unrealized P&L from open positions).
    
    Returns:
        Current total equity in USD
    """
    balance = get_available_balance()
    capital = balance.get("total_usd", 0)
    
    # Add unrealized P&L from open positions
    unrealized_pnl = 0.0
    try:
        # Get Kraken positions for accurate P&L
        if POSITION_TRACKING_ENABLED and kraken_positions.get('positions'):
            for symbol, pos in kraken_positions['positions'].items():
                if not pos:
                    continue
                # Use unrealized_pnl from Kraken if available
                if 'unrealized_pnl' in pos:
                    unrealized_pnl += pos.get('unrealized_pnl', 0)
                else:
                    # Fallback: calculate from open_positions tracking
                    if symbol in open_positions:
                        tracked_pos = open_positions[symbol]
                        entry_price = tracked_pos.get('entry_price', 0)
                        direction = tracked_pos.get('direction', 'LONG')
                        contracts = tracked_pos.get('size', 0)
                        current_price = tracked_pos.get('current_price', entry_price)
                        
                        if entry_price > 0 and contracts > 0:
                            if direction == "LONG":
                                pnl = (current_price - entry_price) * contracts
                            else:
                                pnl = (entry_price - current_price) * contracts
                            unrealized_pnl += pnl
    except Exception as e:
        # Silently fail - use capital only
        pass
    
    return capital + unrealized_pnl

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

    allowed, reason = sunday_engine.is_sunday_trading_time()
    if not allowed:
        return {"success": False, "reason": f"SUNDAY_BLOCK: {reason}"}

    if PROFIT_MODE_ENABLED:
        should_trade, trade_reason = profit_tracker.should_trade_more()
        if not should_trade:
            print(f"  ⏸️ PROFIT MODE: Skipping trade - {trade_reason}")
            return {"success": False, "reason": f"PROFIT_MODE_STOP: {trade_reason}"}
    
    if not POSITION_TRACKING_ENABLED:
        return {"success": False, "reason": "NO_API_KEYS"}
    
    # Circuit breaker (cooldown / consecutive losses)
    is_paused, pause_reason = check_circuit_breaker()
    if is_paused:
        return {"success": False, "reason": f"CIRCUIT_BREAKER: {pause_reason}"}
    
    # Check risk limits (v8.9.18 - percentage based)
    if auto_trading_state['is_paused']:
        return {"success": False, "reason": f"RISK_LIMIT_PAUSED: {auto_trading_state['pause_reason']}"}
    
    # v8.9.21 #4: FAIL-CLOSED - Halt trading if balance unknown
    balance = get_available_balance()
    if balance.get("fetch_failed") and balance.get("consecutive_failures", 0) >= 3:
        print(f"  🚫 FAIL-CLOSED: Balance fetch failed {balance['consecutive_failures']}x - trading halted")
        log_risk_event("BALANCE_FETCH_FAILED", f"Balance unknown after {balance['consecutive_failures']} failures - trading halted", "critical")
        return {"success": False, "reason": "BALANCE_UNKNOWN_FAIL_CLOSED"}
    if balance.get("total_usd", 0) <= 0:
        auto_trading_state['is_paused'] = True
        auto_trading_state['pause_type'] = "EMERGENCY"
        auto_trading_state['pause_reason'] = "BALANCE_ZERO_OR_UNKNOWN"
        log_risk_event("BALANCE_ZERO", "Balance is zero or unavailable - trading halted", "critical")
        return {"success": False, "reason": "BALANCE_ZERO_OR_UNKNOWN"}
    
    capital = balance.get("total_usd", 0)
    
    # v8.9.25: Maximum Drawdown Protection - Check BEFORE other limits
    current_equity = get_current_equity()
    update_peak_equity(current_equity)  # Update peak if new high reached
    
    is_drawdown_paused, drawdown_reason, drawdown_pct = check_max_drawdown(current_equity)
    if is_drawdown_paused:
        if auto_trading_state['pause_type'] != "MAX_DRAWDOWN":
            # Only log and alert if this is a new drawdown pause
            auto_trading_state['is_paused'] = True
            auto_trading_state['pause_type'] = "MAX_DRAWDOWN"
            auto_trading_state['pause_reason'] = drawdown_reason
            log_risk_event("MAX_DRAWDOWN", drawdown_reason, "critical")
            print(f"  📉 MAX_DRAWDOWN: {drawdown_reason}")
        return {"success": False, "reason": "MAX_DRAWDOWN_LIMIT"}
    
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
    if kraken_positions.get("fetch_failed") and kraken_positions.get("consecutive_failures", 0) >= 2:
        auto_trading_state['is_paused'] = True
        auto_trading_state['pause_type'] = "EMERGENCY"
        auto_trading_state['pause_reason'] = "POSITION_FETCH_FAILED"
        log_risk_event("POSITION_FETCH_FAILED", "Could not fetch Kraken positions - trading halted", "critical")
        return {"success": False, "reason": "POSITION_FETCH_FAILED"}
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
    
    # ================================
    # STRATEGY HEALTH CHECK (CRITICAL)
    # ================================
    # Note: Health check is done in signal generation, but verify here as well
    strategy_name = kwargs.get('strategy_name', 'TREND_CONTINUATION')
    size_multiplier = kwargs.get('size_multiplier', 1.0)
    
    # Double-check health (defense in depth)
    health = strategy_health_engine.evaluate_strategy(strategy_name)
    
    if health.status == "DISABLED":
        print(f"  🚫 STRATEGY HEALTH: {strategy_name} is DISABLED - {health.reason}")
        return {"success": False, "reason": f"STRATEGY_DISABLED: {health.reason}"}
    
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
        
        # Apply size multiplier from signal generation (Strategy Health + Rebound restrictions)
        if size_multiplier != 1.0:
            original_size = position_size_usd
            position_size_usd *= size_multiplier
            print(f"  ⚠️ SIZE MULTIPLIER: Position reduced ${original_size:.0f} → ${position_size_usd:.0f} ({size_multiplier:.0%})")
            if health.status == "WARNING":
                print(f"     Reason: Strategy {strategy_name} is WARNING - {health.reason}")
        
        # v8.9.23: Apply SCALP_REBOUND size multiplier if active
        scalp_rebound_multiplier = kwargs.get('scalp_rebound_multiplier', 1.0)
        if scalp_rebound_multiplier < 1.0:
            original_size = position_size_usd
            position_size_usd = position_size_usd * scalp_rebound_multiplier
            print(f"     🔄 SCALP_REBOUND: Position reduced ${original_size:.0f} → ${position_size_usd:.0f} ({scalp_rebound_multiplier:.0%})")
        
        # Apply risk modifier from 4H trend context (CASHFLOW BOT)
        risk_modifier = kwargs.get('risk_modifier', 1.0)
        if risk_modifier < 1.0:
            original_size = position_size_usd
            position_size_usd = position_size_usd * risk_modifier
            print(f"     📊 4H RISK MODIFIER: Position reduced ${original_size:.0f} → ${position_size_usd:.0f} ({risk_modifier:.0%})")

        if PROFIT_MODE_ENABLED:
            profit_multiplier = profit_tracker.get_position_multiplier()
            if profit_multiplier != 1.0:
                original_size = position_size_usd
                position_size_usd = position_size_usd * profit_multiplier
                print(f"     💰 PROFIT MODE: Position adjusted ${original_size:.0f} → ${position_size_usd:.0f} ({profit_multiplier:.2f}x)")
        
        # Calculate position size in contracts
        position_size = position_size_usd / price

        sunday_adjusted = sunday_engine.adjust_for_sunday({
            "position_size": position_size,
            "leverage": selected_leverage
        })
        position_size = sunday_adjusted.get("position_size", position_size)
        selected_leverage = int(sunday_adjusted.get("leverage", selected_leverage))

        if PROFIT_MODE_ENABLED and tp1 and price > 0:
            expected_profit_usd = abs(tp1 - price) * position_size
            expected_profit_eur = expected_profit_usd * get_usd_eur_rate()
            if expected_profit_eur < MIN_PROFIT_PER_TRADE_EUR:
                print(f"  ⏸️ PROFIT MODE: TP1 profit too small ({expected_profit_eur:.2f}€ < {MIN_PROFIT_PER_TRADE_EUR}€)")
                return {"success": False, "reason": "PROFIT_MODE_MIN_PROFIT"}
        
        # Determine order side
        side = "buy" if direction == "LONG" else "sell"
        
        # v8.9.6: Set leverage BEFORE placing order (Kraken uses account-level leverage)
        try:
            await safe_exchange_call(exchange.set_leverage, selected_leverage, symbol, timeout=5)
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
                
                limit_price = price
                max_wait = LIMIT_ORDER_WAIT_SECONDS
                poll_interval = LIMIT_CHASE_INTERVAL_SECONDS
                max_steps = max(1, LIMIT_CHASE_MAX_STEPS)
                total_wait = 0
                
                for i in range(max_steps):
                    await asyncio.sleep(poll_interval)
                    total_wait += poll_interval
                    try:
                        order_check = await safe_exchange_call(exchange.fetch_order, order_id, symbol, timeout=5)
                        if order_check is None:
                            print(f"     ⚠️ Order check timeout")
                            continue
                        order_status = order_check.get('status', 'unknown')
                        filled_amount = order_check.get('filled', 0) or 0
                        actual_entry_price = order_check.get('average', price) or price
                        
                        if order_status in ['closed', 'filled']:
                            print(f"     ✅ LIMIT order filled @ ${actual_entry_price:.2f} after {total_wait}s")
                            position_size = filled_amount
                            break
                        elif order_status == 'canceled':
                            return {"success": False, "reason": "LIMIT_ORDER_CANCELED"}
                        
                        # Chase price if still open
                        if order_status == 'open':
                            try:
                                ticker = await safe_exchange_call(exchange.fetch_ticker, symbol, timeout=5)
                                last_price = None
                                if ticker:
                                    last_price = ticker.get('last') or ticker.get('mark') or ticker.get('close')
                                
                                if last_price:
                                    if direction == "LONG":
                                        chase_price = last_price * (1 + LIMIT_CHASE_STEP_PCT / 100)
                                        max_price = price * (1 + LIMIT_MAX_SLIPPAGE_PCT / 100)
                                        new_limit_price = min(chase_price, max_price)
                                    else:
                                        chase_price = last_price * (1 - LIMIT_CHASE_STEP_PCT / 100)
                                        min_price = price * (1 - LIMIT_MAX_SLIPPAGE_PCT / 100)
                                        new_limit_price = max(chase_price, min_price)
                                    
                                    if abs(new_limit_price - limit_price) / limit_price * 100 >= 0.03:
                                        await safe_exchange_call(exchange.cancel_order, order_id, symbol, timeout=5)
                                        order = exchange.create_order(
                                            symbol=symbol,
                                            type='limit',
                                            side=side,
                                            amount=position_size,
                                            price=new_limit_price
                                        )
                                        order_id = order.get('id')
                                        limit_price = new_limit_price
                                        print(f"     🎯 LIMIT chase #{i+1}: ${limit_price:.2f}")
                            except Exception as e:
                                print(f"     ⚠️ Chase error: {e}")
                        
                        if total_wait >= max_wait:
                            break
                    except Exception as e:
                        print(f"     ⚠️ Check error: {e}")
                
                if order_status not in ['closed', 'filled']:
                    # Timeout - cancel order
                    print(f"     ⏰ LIMIT order timeout after {total_wait}s - cancelling")
                    try:
                        await safe_exchange_call(exchange.cancel_order, order_id, symbol, timeout=5)
                    except:
                        pass
                    
                    allow_fallback = (
                        LIMIT_FALLBACK_TO_MARKET
                        and leverage_tier == "STRONG"
                        and signal_score >= LIMIT_FALLBACK_MIN_SCORE
                    )
                    
                    if allow_fallback:
                        print(f"     ⚡ LIMIT fallback → MARKET (score {signal_score}, tier {leverage_tier})")
                        order = exchange.create_order(
                            symbol=symbol,
                            type='market',
                            side=side,
                            amount=position_size
                        )
                        order_id = order.get('id')
                        order_status = order.get('status', 'unknown')
                        filled_amount = order.get('filled', 0) or 0
                        actual_entry_price = order.get('average', price) or price
                        
                        if order_status not in ['closed', 'filled'] or filled_amount < position_size * 0.5:
                            try:
                                await asyncio.sleep(1)
                                order_check = await safe_exchange_call(exchange.fetch_order, order_id, symbol, timeout=5)
                                if order_check:
                                    order_status = order_check.get('status', 'unknown')
                                    filled_amount = order_check.get('filled', 0) or 0
                                    actual_entry_price = order_check.get('average', price) or price
                            except Exception as verify_err:
                                print(f"     ⚠️ Market fallback verify error: {verify_err}")
                            
                            if order_status not in ['closed', 'filled'] or filled_amount < position_size * 0.5:
                                return {"success": False, "reason": f"LIMIT_FALLBACK_NOT_FILLED: {order_status}"}
                    else:
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
                    await asyncio.sleep(1)  # Wait for exchange to process
                    order_check = await safe_exchange_call(exchange.fetch_order, order_id, symbol, timeout=5)
                    if order_check is None:
                        print(f"     ⚠️ Order verification timeout - assuming failed")
                        return {"success": False, "reason": "ORDER_VERIFICATION_TIMEOUT"}
                    order_status = order_check.get('status', 'unknown')
                    filled_amount = order_check.get('filled', 0) or 0
                    actual_entry_price = order_check.get('average', price) or price
                    
                    if order_status not in ['closed', 'filled'] or filled_amount < position_size * 0.5:
                        # Order failed or partially filled - cancel and abort
                        print(f"     🔴 ORDER FAILED: Trying to cancel...")
                        try:
                            await safe_exchange_call(exchange.cancel_order, order_id, symbol, timeout=5)
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
            sl_order = await safe_exchange_call(
                exchange.create_order,
                symbol, 'stop', sl_side, position_size, sl, None,
                {'triggerPrice': sl, 'reduceOnly': True},
                timeout=10, max_retries=3
            )
            if sl_order is None:
                raise Exception("SL order timeout after retries")
            sl_order_id = sl_order.get('id')
            print(f"     🛡️ EXCHANGE SL placed @ ${sl:.2f} (ID: {sl_order_id})")
        except Exception as sl_err:
            # Critical: If SL order fails, close position immediately
            print(f"     ⚠️ SL ORDER FAILED: {sl_err}")
            try:
                # Emergency close - SL is mandatory
                close_side = "sell" if direction == "LONG" else "buy"
                close_order = await safe_exchange_call(
                    exchange.create_order,
                    symbol, 'market', close_side, position_size, None, None,
                    {'reduceOnly': True},
                    timeout=10
                )
                if close_order:
                    print(f"     🚨 EMERGENCY CLOSE - Position closed due to SL failure")
                    return {"success": False, "reason": "SL_ORDER_FAILED_EMERGENCY_CLOSE"}
                else:
                    print(f"     🔴 CRITICAL: Emergency close also failed!")
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
        
        # v8.9.25: NET PROFIT ENGINE - Check if TP1 makes sense after fees
        # Calculate risk in USD
        if direction == "LONG":
            sl_distance_pct = abs(price - sl) / price
        else:
            sl_distance_pct = abs(sl - price) / price
        risk_usd = position_size_usd * sl_distance_pct
        
        # Get RR from exit levels (TP1 is typically 1R)
        if exit_levels.tp1:
            if direction == "LONG":
                tp1_distance = exit_levels.tp1 - price
                sl_distance = price - sl
            else:
                tp1_distance = price - exit_levels.tp1
                sl_distance = sl - price
            rr_target = tp1_distance / sl_distance if sl_distance > 0 else 1.0
        else:
            rr_target = 1.0  # Default to 1R
        
        # Create net profit context
        net_ctx = NetProfitContext(
            position_size_usd=position_size_usd,
            risk_usd=risk_usd,
            rr_target=rr_target,
            fee_config=FeeConfig(),
            min_net_profit_usd=0.50  # FUND minimum
        )
        
        # Optimize RR after fees
        net_result = optimize_rr_after_fees(net_ctx)
        
        # If TP is too small after fees, adjust trade mode or TP1
        if not net_result.valid:
            print(f"     ⚠️ NET PROFIT CHECK: TP1 too small after fees (${net_result.estimated_net_profit_usd:.2f} < ${net_ctx.min_net_profit_usd:.2f})")
            print(f"     🔄 Required RR: {net_result.adjusted_rr:.2f} (current: {rr_target:.2f})")
            
            # If required RR is too high (>2.0), switch to RUNNER_ONLY mode
            if net_result.adjusted_rr > 2.0:
                trade_mode = "RUNNER_ONLY"
                print(f"     🎯 Switching to RUNNER_ONLY mode (skip TP1/TP2, go to TP3)")
            elif exit_levels.tp1:
                # Adjust TP1 to meet minimum profit
                if direction == "LONG":
                    tp1 = price + (price - sl) * net_result.adjusted_rr
                else:
                    tp1 = price - (sl - price) * net_result.adjusted_rr
                print(f"     📊 Adjusted TP1: ${tp1:.2f} (RR: {net_result.adjusted_rr:.2f})")
        else:
            print(f"     ✅ NET PROFIT CHECK: TP1 OK (Net: ${net_result.estimated_net_profit_usd:.2f}, Fees: ${net_result.fees_usd:.2f})")
        
        # v8.9.25: EXPECTANCY ENGINE - Check if trade has positive expectancy
        # Get historical win rate
        win_rate_data = get_win_rate()
        # get_win_rate() returns win_rate as percentage (e.g., 48.5), convert to decimal (0.485)
        historical_win_rate = win_rate_data.get("win_rate", 0) / 100.0 if win_rate_data.get("win_rate", 0) > 1.0 else win_rate_data.get("win_rate", 0)
        
        # Use adjusted RR if net profit check adjusted it
        final_rr = net_result.adjusted_rr if not net_result.valid and net_result.adjusted_rr <= 2.0 else rr_target
        
        # Only check expectancy if we have enough historical data (at least 10 trades)
        if win_rate_data.get("total", 0) >= 10:
            exp_ctx = ExpectancyContext(
                win_rate=historical_win_rate,
                rr=final_rr,
                min_expectancy=0.10  # FUND minimum
            )
            
            exp_result = evaluate_expectancy(exp_ctx)
            
            if not exp_result.valid:
                print(f"     🚫 EXPECTANCY CHECK: Trade blocked (Expectancy: {exp_result.expectancy:.3f} < {exp_ctx.min_expectancy})")
                print(f"        Win Rate: {historical_win_rate*100:.1f}% | RR: {final_rr:.2f}")
                return {"success": False, "reason": f"EXPECTANCY_FAIL ({exp_result.expectancy:.3f})"}
            else:
                print(f"     ✅ EXPECTANCY CHECK: Trade OK (Expectancy: {exp_result.expectancy:.3f}, Win Rate: {historical_win_rate*100:.1f}%, RR: {final_rr:.2f})")
        else:
            print(f"     ⚠️ EXPECTANCY CHECK: Skipped (insufficient data: {win_rate_data.get('total', 0)} trades, need 10+)")
        
        # Track position for trailing stop management (lock still held from check above)
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
            "strategy_name": kwargs.get('strategy_name', 'TREND_CONTINUATION'),  # Strategy identification
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

async def cancel_existing_sl_orders_for_symbol(symbol):
    """Cancel any existing reduce-only/stop orders for symbol (e.g. user-set SL). Used before placing first trailing SL."""
    try:
        orders = await safe_exchange_call(exchange.fetch_open_orders, symbol, timeout=5)
        if not orders:
            return
        cancelled = 0
        for o in orders:
            info = o.get('info', {})
            if info.get('reduceOnly') or o.get('reduceOnly'):
                try:
                    await safe_exchange_call(exchange.cancel_order, o['id'], symbol, timeout=3)
                    cancelled += 1
                    print(f"     🗑️ Cancelled existing SL/TP order (ID: {o['id']})")
                except Exception:
                    pass
    except Exception as e:
        print(f"     ⚠️ Could not fetch/cancel existing orders: {e}")

async def update_exchange_sl(symbol, new_sl_price, position_size):
    """
    v8.9.21: Update exchange-side stop loss order when trailing stop moves.
    
    Cancel old SL order and place new one at updated price.
    TRAILING_STOP_ON_EXCHANGE: For Kraken-imported positions (no exchange_sl_order_id),
    cancel any existing reduce-only orders first, then place our trailing SL.
    """
    global open_positions
    
    async with position_lock:
        if symbol not in open_positions:
            return {"success": False, "reason": "NO_LOCAL_POSITION"}
        
        position = open_positions[symbol]
        old_sl_order_id = position.get('exchange_sl_order_id')
        direction = position['direction']
        remaining_size = position.get('remaining_size', position_size)
    
    # Operations outside lock to avoid blocking
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # First-time placement: cancel any existing reduce-only orders (user's manual SL)
            if not old_sl_order_id:
                await cancel_existing_sl_orders_for_symbol(symbol)
            # Cancel our old SL order if exists
            elif old_sl_order_id:
                try:
                    await safe_exchange_call(exchange.cancel_order, old_sl_order_id, symbol, timeout=5)
                    print(f"     🗑️ Old SL order cancelled (ID: {old_sl_order_id})")
                except Exception as cancel_err:
                    print(f"     ⚠️ Could not cancel old SL: {cancel_err}")
                    # Continue anyway - new order will replace it
            
            # Place new SL order (Kraken Futures: type 'stop' with triggerPrice)
            sl_side = "sell" if direction == "LONG" else "buy"
            sl_order = await safe_exchange_call(
                exchange.create_order,
                symbol, 'stop', sl_side, remaining_size, new_sl_price, None,
                {'triggerPrice': new_sl_price, 'reduceOnly': True},
                timeout=10, max_retries=2
            )
            
            if sl_order is None:
                if attempt < max_retries - 1:
                    print(f"     ⚠️ SL update failed, retrying ({attempt+1}/{max_retries})...")
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                else:
                    log_risk_event("SL_UPDATE_FAILED", f"Failed to update SL for {symbol} after {max_retries} attempts", "critical")
                    return {"success": False, "reason": "SL_UPDATE_FAILED_AFTER_RETRIES"}
            
            new_sl_order_id = sl_order.get('id')
            
            # VERIFY: Check that order actually exists
            try:
                verify_order = await safe_exchange_call(exchange.fetch_order, new_sl_order_id, symbol, timeout=5)
                if verify_order is None:
                    print(f"     ⚠️ Could not verify SL order - retrying...")
                    if attempt < max_retries - 1:
                        continue
            except:
                if attempt < max_retries - 1:
                    continue
            
            # Update position tracking (with lock)
            async with position_lock:
                if symbol in open_positions:
                    open_positions[symbol]['exchange_sl_order_id'] = new_sl_order_id
                    open_positions[symbol]['current_sl'] = new_sl_price
            
            print(f"     🛡️ Exchange SL updated to ${new_sl_price:.2f} (ID: {new_sl_order_id})")
            
            return {"success": True, "order_id": new_sl_order_id, "new_sl": new_sl_price}
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"     ⚠️ SL update error (attempt {attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(1 * (attempt + 1))
                continue
            else:
                print(f"     ⚠️ Failed to update exchange SL after {max_retries} attempts: {e}")
                log_risk_event("SL_UPDATE_ERROR", f"SL update failed for {symbol}: {e}", "critical")
                return {"success": False, "reason": str(e)}
    
    return {"success": False, "reason": "SL_UPDATE_FAILED"}

async def cancel_exchange_sl(symbol):
    """v8.9.21: Cancel exchange-side SL order when position is closed."""
    global open_positions
    
    if symbol not in open_positions:
        return {"success": False, "reason": "NO_LOCAL_POSITION"}
    
    position = open_positions[symbol]
    sl_order_id = position.get('exchange_sl_order_id')
    
    if not sl_order_id:
        return {"success": True, "reason": "NO_SL_ORDER"}
    
    # Cancel outside lock
    try:
        await safe_exchange_call(exchange.cancel_order, sl_order_id, symbol, timeout=5)
        async with position_lock:
            if symbol in open_positions:
                open_positions[symbol]['exchange_sl_order_id'] = None
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
        
        # Get current position from Kraken (with timeout)
        positions = await safe_exchange_call(exchange.fetch_positions, timeout=10)
        if positions is None:
            return {"success": False, "reason": "FETCH_POSITIONS_TIMEOUT"}
        
        position_data = None
        for pos in positions:
            pos_symbol = pos.get('symbol', '')
            if symbol in pos_symbol or pos.get('info', {}).get('symbol') == symbol:
                if pos['contracts'] and float(pos['contracts']) > 0:
                    position_data = pos
                    break
        
        if not position_data:
            # Remove from local tracking if exists
            async with position_lock:
                if symbol in open_positions:
                    del open_positions[symbol]
            return {"success": False, "reason": "NO_POSITION_FOUND"}
        
        total_size = float(position_data['contracts'])
        pos_side = position_data['side']
        entry_price = float(position_data['entryPrice']) if position_data['entryPrice'] else 0
        unrealized_pnl = float(position_data['unrealizedPnl']) if position_data['unrealizedPnl'] else 0
        
        # Close order (opposite side) - with timeout
        close_side = "sell" if pos_side == 'long' else "buy"
        direction = "LONG" if pos_side == 'long' else "SHORT"
        
        order = await safe_exchange_call(
            exchange.create_order,
            symbol, 'market', close_side, total_size, None, None,
            {'reduceOnly': True},
            timeout=10
        )
        if order is None:
            return {"success": False, "reason": "CLOSE_ORDER_TIMEOUT"}
        
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

        pnl_eur = pnl * get_usd_eur_rate()
        asset_performance.update(symbol, pnl_eur)
        sunday_engine.update_sunday_stats({
            "asset": symbol,
            "profit": pnl
        })

        if PROFIT_MODE_ENABLED:
            trade_profit = pnl_eur
            position_basis_price = entry_price or close_price
            position_size_eur = (position_basis_price * total_size) * get_usd_eur_rate()
            profit_tracker.add_trade_result(trade_profit, position_size_eur)
            current_target = auto_adjust_targets.current_target if AUTO_ADJUST_TARGETS_ENABLED else DAILY_PROFIT_TARGET_EUR
            print(f"     💰 PROFIT MODE: Daily {profit_tracker.daily_profit:.2f}€ / {current_target:.2f}€")
        
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
        sunday_engine.reset_daily_stats()
        if PROFIT_MODE_ENABLED:
            current_target = auto_adjust_targets.current_target if AUTO_ADJUST_TARGETS_ENABLED else DAILY_PROFIT_TARGET_EUR
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        DailyReport.send_daily_summary(
                            profit_tracker,
                            asset_performance,
                            current_target,
                            WEEKLY_PROFIT_TARGET_EUR,
                            send_telegram_status
                        )
                    )
            except RuntimeError:
                pass

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
        
        # Note: Peak equity is NOT reset daily - it tracks "all-time" high
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
        
        # Reset weekly pause (but not max drawdown - that requires manual reset)
        if auto_trading_state['pause_type'] == "WEEKLY":
            auto_trading_state['is_paused'] = False
            auto_trading_state['pause_reason'] = None
            auto_trading_state['pause_type'] = None
        
        # Note: Peak equity is NOT reset weekly - it tracks "all-time" high
        
        auto_trading_state['last_weekly_reset'] = now.date()

# ================================
# KRAKEN POSITION TRACKING
# ================================
async def fetch_kraken_positions():
    """Fetch open positions from Kraken Futures API"""
    global kraken_positions
    
    if not POSITION_TRACKING_ENABLED:
        return {}
    
    if DISABLE_KRAKEN_IN_SIGNAL_ONLY and not AUTO_TRADING_ENABLED and not KRAKEN_OBSERVER_MODE:
        return kraken_positions['positions']
    
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
        kraken_positions['fetch_failed'] = False
        kraken_positions['consecutive_failures'] = 0
        
        if new_positions:
            pos_list = [f"{ASSET_NAMES.get(s, s)} {p['direction']} {p['leverage']}x" for s, p in new_positions.items()]
            print(f"📍 Kraken positions: {', '.join(pos_list)}")
        
        return new_positions
        
    except Exception as e:
        print(f"⚠️ Error fetching Kraken positions: {e}")
        kraken_positions['fetch_failed'] = True
        kraken_positions['consecutive_failures'] = kraken_positions.get('consecutive_failures', 0) + 1
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
                "trade_mode": TRADE_MODE,  # Use global mode for Kraken-imported positions
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
                # WITH DOUBLE-COUNTING PREVENTION
                contracts = pos.get('size', 0)
                if contracts > 0 and entry_price > 0:
                    if direction == "LONG":
                        pnl_usd = (current_price - entry_price) * contracts
                    else:
                        pnl_usd = (entry_price - current_price) * contracts
                else:
                    # Fallback: estimate from percentage and margin
                    pnl_usd = size_usd * (pnl_pct / 100)
                
                # Check if P&L already counted (prevent double counting)
                position_id = pos.get('position_id')
                if not pos.get('pnl_counted', False):
                    auto_trading_state['daily_pnl'] += pnl_usd
                    auto_trading_state['weekly_pnl'] += pnl_usd
                    auto_trading_state['weekly_trades'] += 1
                    if is_win:
                        auto_trading_state['daily_wins'] += 1
                        auto_trading_state['weekly_wins'] += 1
                    else:
                        auto_trading_state['daily_losses'] += 1
                        auto_trading_state['weekly_losses'] += 1
                    pos['pnl_counted'] = True  # Mark as counted
                else:
                    print(f"  ⚠️ P&L already counted for {symbol} (position_id: {position_id}) - skipping")
                
                print(f"  💰 P&L: ${pnl_usd:+.2f} | Daily: ${auto_trading_state['daily_pnl']:.2f}")
                
                # v8.9.25: Update peak equity after position close
                current_equity = get_current_equity()
                update_peak_equity(current_equity)
                
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
                
                # Create position tracking (with lock)
                async with position_lock:
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
                    "strategy_name": "TREND_CONTINUATION",
                    "trade_mode": TRADE_MODE,
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
        "strategy_name": signal.get('strategy_name', 'TREND_CONTINUATION'),  # Strategy identification
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

def _get_trailing_model(mode):
    """Get trailing model config for trade mode. Fallback to CASHFLOW if unknown."""
    cfg = TRAILING_MODEL.get(mode, TRAILING_MODEL.get("CASHFLOW", {}))
    return cfg or TRAILING_MODEL["CASHFLOW"]


def _get_trailing_distance_pct(position, current_price, model):
    """Trailing distance % - CASHFLOW: dynamic 0.8-1.2%, SWING: fixed 2.5-3.5%"""
    direction = position['direction']
    entry = position['entry_price']
    if direction == "LONG":
        profit_pct = ((current_price - entry) / entry) * 100
    else:
        profit_pct = ((entry - current_price) / entry) * 100

    if "activation_at" in model and model["activation_at"] == "TP2":
        return model.get("distance_pct", 3.0)
    # CASHFLOW: dynamic 0.8-1.2% based on profit
    dmin = model.get("distance_min", 0.8)
    dmax = model.get("distance_max", 1.2)
    act = model.get("activation_pct", 0.9)
    if profit_pct <= act:
        return dmin
    # Linear: more profit = slightly wider trail
    t = min(1.0, (profit_pct - act) / 2.0)
    return dmin + (dmax - dmin) * t


def calculate_trailing_sl(position, current_price):
    """Calculate new trailing stop level. Uses TRAILING_MODEL (CASHFLOW/SWING)."""
    mode = position.get("trade_mode", TRADE_MODE)
    model = _get_trailing_model(mode)
    direction = position['direction']
    distance_pct = _get_trailing_distance_pct(position, current_price, model)
    trailing_distance = current_price * (distance_pct / 100)

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

            model = _get_trailing_model(position.get("trade_mode", TRADE_MODE))
            be_at = model.get("breakeven_at", "TP1")
            be_buffer = model.get("breakeven_buffer_pct", 0.0) / 100.0

            if (BREAKEVEN_AT_TP1 or be_at in ("TP1", "TP1_BUFFERED")) and not position['breakeven_active']:
                if be_at == "TP1_BUFFERED" and be_buffer > 0:
                    position['current_sl'] = entry * (1 + be_buffer)
                else:
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

        # Check trailing activation and update (CASHFLOW vs SWING)
        model = _get_trailing_model(position.get("trade_mode", TRADE_MODE))
        activate_now = False
        if model.get("activation_at") == "TP2":
            activate_now = position['tp2_hit']
        else:
            act_pct = model.get("activation_pct", 0.9)
            activate_now = profit_pct >= act_pct

        if TRAILING_ENABLED and activate_now:
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

            model = _get_trailing_model(position.get("trade_mode", TRADE_MODE))
            be_at = model.get("breakeven_at", "TP1")
            be_buffer = model.get("breakeven_buffer_pct", 0.0) / 100.0

            if (BREAKEVEN_AT_TP1 or be_at in ("TP1", "TP1_BUFFERED")) and not position['breakeven_active']:
                if be_at == "TP1_BUFFERED" and be_buffer > 0:
                    position['current_sl'] = entry * (1 - be_buffer)
                else:
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

        # Check trailing activation and update (CASHFLOW vs SWING)
        model = _get_trailing_model(position.get("trade_mode", TRADE_MODE))
        activate_now = False
        if model.get("activation_at") == "TP2":
            activate_now = position['tp2_hit']
        else:
            act_pct = model.get("activation_pct", 0.9)
            activate_now = profit_pct >= act_pct

        if TRAILING_ENABLED and activate_now:
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

def detect_macro_market_regime():
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
        regime_ctx = RegimeV2Context(
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
    
    now = datetime.now(timezone.utc)
    cooldown_until = circuit_state.get("cooldown_until")
    if cooldown_until:
        if now < cooldown_until:
            circuit_state["is_paused"] = True
            circuit_state["pause_reason"] = f"COOLDOWN until {cooldown_until.strftime('%H:%M UTC')}"
            return True, circuit_state["pause_reason"]
        else:
            circuit_state["cooldown_until"] = None
            circuit_state["consecutive_losses"] = 0
    
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
        circuit_state["cooldown_until"] = None
    else:
        circuit_state["consecutive_losses"] += 1
        circuit_state["last_signal_result"] = "LOSS"
        
        if LOSS_COOLDOWN_ENABLED:
            now = datetime.now(timezone.utc)
            if circuit_state["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
                circuit_state["cooldown_until"] = now + timedelta(minutes=LOSS_COOLDOWN_HARD_MINUTES)
            elif circuit_state["consecutive_losses"] >= LOSS_COOLDOWN_AFTER:
                circuit_state["cooldown_until"] = now + timedelta(minutes=LOSS_COOLDOWN_MINUTES)
        
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
    circuit_state["cooldown_until"] = None

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

        # Bounce long in BEAR (support + bullish reversal)
        support_bounce = signal_data.get('support_bounce', False) if signal_data else False
        has_bullish_reversal = signal_data.get('has_bullish_reversal', False) if signal_data else False
        allow_bounce_long = (
            BOUNCE_LONG_ENABLED
            and support_bounce
            and has_bullish_reversal
            and confluence_score >= BOUNCE_LONG_MIN_CONFLUENCE
        )
        
        if is_extreme_oversold:
            # Allow contrarian LONG - skip bear market blocking
            reasons.append("CONTRARIAN_OVERSOLD")
        elif allow_bounce_long:
            reasons.append("BOUNCE_LONG")
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
                confluence_score = signal_data.get('confluence_score', 0)
                
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
                    volatility_level=volatility_level,
                    direction=direction,
                    confluence_score=confluence_score
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
# Cashflow-friendly consolidation thresholds
CONSOLIDATION_ATR_THRESHOLD = 0.40     # Slightly stricter volatility floor
CONSOLIDATION_RANGE_EDGE_PCT = 35      # Require closer to range edges
CONSOLIDATION_MOMENTUM_MIN = 28        # Require a bit more momentum
CONSOLIDATION_ADX_MIN = 16             # Require stronger trend

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
    atr_thr, range_edge_pct, momentum_min, adx_min = get_adaptive_consolidation_thresholds()
    
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
        if distance_pct > range_edge_pct:
            consolidation_score += 30
            reasons.append(f"MID_RANGE ({100-distance_pct:.0f}% from edge)")
        
        # 3. Check ATR volatility
        atr = calc_atr(df, period=14)
        if atr is not None and len(atr) > 0:
            atr_value = atr.iloc[-1]
            atr_pct = (atr_value / current_price) * 100
            if atr_pct < atr_thr:
                consolidation_score += 25
                reasons.append(f"LOW_VOL (ATR={atr_pct:.2f}%<{atr_thr:.2f}%)")
        
        # 4. Check ADX (trend strength)
        adx, _, _ = calc_adx(df, period=14)
        if adx is not None and len(adx) > 0:
            adx_value = adx.iloc[-1]
            if adx_value < adx_min:
                consolidation_score += 25
                reasons.append(f"WEAK_TREND (ADX={adx_value:.0f}<{adx_min})")
        
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
            if wave_score < momentum_min:
                consolidation_score += 20
                reasons.append(f"WEAK_MOMENTUM (Wave={wave_score}<{momentum_min})")
        
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

def detect_support_bounce(df, current_price: float, relax_level: int = 0) -> tuple:
    """
    Simple support-bounce detector for BEAR bounce longs.
    Returns (is_bounce, distance_pct_from_support).
    """
    try:
        if df is None or len(df) < 30:
            return False, None
        lookback = 48
        recent_low = df['low'].tail(lookback).min()
        if not recent_low or current_price <= 0:
            return False, None
        distance_pct = (current_price - recent_low) / current_price * 100
        last = df.iloc[-1]
        bullish_candle = last['close'] > last['open']
        threshold = 0.5 + (0.2 * max(0, int(relax_level)))
        is_bounce = bullish_candle and 0 <= distance_pct <= threshold
        return is_bounce, distance_pct
    except Exception:
        return False, None

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


def bearish_impulse(df, lookback: int = 2, avg_range_period: int = 20) -> bool:
    if df is None or len(df) < (avg_range_period + 2):
        return False

    ranges = (df["high"] - df["low"])
    avg_range = ranges.iloc[-(avg_range_period + 2):-2].mean()
    if avg_range is None or avg_range <= 0:
        return False

    candles = df[["open", "high", "low", "close"]].to_dict("records")
    impulse = candles[-2]
    next_candle = candles[-1]

    impulse_range = impulse["high"] - impulse["low"]
    if impulse_range <= 0:
        return False

    big_range = impulse_range >= (1.5 * avg_range)
    strong_close = impulse["close"] <= (impulse["low"] + 0.25 * impulse_range)

    swings = detect_swings(candles, lookback=lookback)
    prev_swing_lows = [
        s for s in swings if s["type"] == "LOW" and s["index"] < (len(candles) - 2)
    ]
    if not prev_swing_lows:
        return False
    prev_swing_low = prev_swing_lows[-1]["price"]
    ll_printed = impulse["low"] < prev_swing_low

    impulse_mid = impulse["low"] + 0.5 * impulse_range
    no_reclaim = next_candle["close"] <= impulse_mid

    return bool(big_range and strong_close and ll_printed and no_reclaim)

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


def classify_htf_structure_state(df_1h, df_htf=None, supply_zone=None, demand_zone=None):
    """
    Classify HTF structure into STRONG_BEAR / BEARISH_TRANSITION / RANGE / STRONG_BULL.
    Uses HTF structure, EMA alignment, and price vs HTF zones/VWAP.
    """
    if df_1h is None or len(df_1h) < 50:
        return "RANGE"

    structure = analyze_market_structure(df_1h, lookback=50)
    close_1h = df_1h["close"].iloc[-1]
    ema21_1h = calc_ema(df_1h["close"], 21).iloc[-1]
    ema50_1h = calc_ema(df_1h["close"], 50).iloc[-1]
    ema_spread = abs(ema21_1h - ema50_1h) / close_1h if close_1h else 0
    ema_tangled = ema_spread < 0.0015

    vwap_series = calc_vwap(df_1h, period=50)
    vwap_1h = vwap_series.iloc[-1] if vwap_series is not None and len(vwap_series) > 0 else close_1h

    below_htf_supply = supply_zone is not None and close_1h < supply_zone.bottom
    above_htf_demand = demand_zone is not None and close_1h > demand_zone.top

    bos_down = structure.get("structure_break") == "BEARISH_BREAK"
    bos_up = structure.get("structure_break") == "BULLISH_BREAK"
    down_structure = structure.get("structure") == "BEARISH" and structure.get("ll_count", 0) >= 1 and structure.get("lh_count", 0) >= 1
    up_structure = structure.get("structure") == "BULLISH" and structure.get("hh_count", 0) >= 1 and structure.get("hl_count", 0) >= 1

    if bos_down and down_structure and (below_htf_supply or close_1h < vwap_1h):
        return "STRONG_BEAR"
    if bos_up and up_structure and (above_htf_demand or close_1h > vwap_1h):
        return "STRONG_BULL"
    if ema_tangled or structure.get("structure") == "NEUTRAL":
        return "RANGE"
    if down_structure or bos_down:
        return "BEARISH_TRANSITION"
    if up_structure or bos_up:
        return "STRONG_BULL"
    return "RANGE"

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

def fetch_ohlcv_sync(symbol, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe}: {e}")
        return None

def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _load_quant_cache():
    cache_path = os.path.join(os.path.dirname(__file__), "quant_cache.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Quant cache load error: {e}")
        return None

def _save_quant_cache(results, correlation, last_update):
    cache_path = os.path.join(os.path.dirname(__file__), "quant_cache.json")
    try:
        payload = {
            "assets": results,
            "correlation": correlation,
            "last_update": last_update.isoformat() if last_update else None
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"Quant cache save error: {e}")

def run_quant_analysis_sync():
    if not QUANT_ENABLED or quant_engine is None:
        return {}, None
    results = {}
    returns_by_asset = {}
    max_daily_candles = 1460  # ~4 years of daily data
    
    for symbol in FUTURES_ASSETS:
        asset_name = ASSET_NAMES.get(symbol, symbol.replace('PF_', '').replace('USD', ''))
        df_daily = fetch_ohlcv_sync(symbol, TIMEFRAME_DAILY, limit=max_daily_candles)
        if df_daily is None or len(df_daily) < 60:
            continue
        
        prices = df_daily['close'].astype(float)
        mc7 = quant_engine.monte_carlo_simulation(prices, n_simulations=350, days=7)
        mc30 = quant_engine.monte_carlo_simulation(prices, n_simulations=350, days=30)
        arima = quant_engine.arima_forecast(prices)
        mr = quant_engine.mean_reversion_analysis(prices)
        fib = quant_engine.fibonacci_levels(prices)
        vol_regime = quant_engine.volatility_regime(prices)
        
        returns = prices.pct_change().dropna()
        returns_by_asset[asset_name] = returns
        annual_vol = _to_float(returns.std() * (365 ** 0.5), 0.0) if len(returns) > 0 else 0.0
        
        fib_signal = fib.get('signal', 0)
        if fib_signal > 0:
            fib_zone = "SUPPORT"
            fib_bias = "BULLISH"
        elif fib_signal < 0:
            fib_zone = "RESISTANCE"
            fib_bias = "BEARISH"
        else:
            fib_zone = "MID"
            fib_bias = "NEUTRAL"
        
        results[asset_name] = {
            "monte_carlo_7d": {
                "prob_up": _to_float(mc7.get("prob_up", 0.5), 0.5),
                "prob_down": _to_float(mc7.get("prob_down", 0.5), 0.5),
                "expected_price": _to_float(mc7.get("expected_price", 0), 0.0),
                "current_price": _to_float(mc7.get("current_price", 0), 0.0),
            },
            "monte_carlo_30d": {
                "expected_price": _to_float(mc30.get("expected_price", 0), 0.0),
                "prob_up_10%": _to_float(mc30.get("prob_up_10%", 0), 0.0),
                "prob_down_10%": _to_float(mc30.get("prob_down_10%", 0), 0.0),
            },
            "mean_reversion": {
                "mean_price": _to_float(mr.get("mean", 0), 0.0),
                "deviation_std": _to_float(mr.get("zscore", 0), 0.0),
            },
            "fibonacci": {
                "zone": fib_zone,
                "bias": fib_bias,
            },
            "returns_analysis": {
                "annual_volatility": annual_vol,
            },
            "monte_carlo_bias": _to_float(mc7.get("bias", 0), 0.0),
            "mean_reversion_signal": _to_float(mr.get("signal", 0), 0.0),
            "arima_trend": _to_float(arima.get("trend", 0), 0.0),
            "fibonacci_signal": _to_float(fib_signal, 0.0),
            "volatility_regime": vol_regime,
        }
    
    correlation = None
    if returns_by_asset:
        try:
            returns_df = pd.DataFrame(returns_by_asset).dropna(how='any')
            if not returns_df.empty:
                raw_corr = returns_df.corr().round(2).to_dict()
                correlation = {
                    k: {kk: _to_float(vv, 0.0) for kk, vv in v.items()}
                    for k, v in raw_corr.items()
                }
        except Exception as e:
            print(f"Quant correlation error: {e}")
    
    return results, correlation

async def get_btc_7d_change() -> float:
    """Get BTC 7d change % using daily candles."""
    df_daily = await fetch_ohlcv("PF_XBTUSD", TIMEFRAME_DAILY, 8)
    if df_daily is None or len(df_daily) < 8:
        return 0.0
    start_price = df_daily['close'].iloc[-8]
    end_price = df_daily['close'].iloc[-1]
    if start_price <= 0:
        return 0.0
    return (end_price - start_price) / start_price * 100

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
    rsi_series = RSIIndicator(close, window=14).rsi()
    rsi_val = rsi_series.iloc[-1] if len(rsi_series) > 0 else 50
    
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
    if QUANT_DIRECTION_ENABLED:
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
    
    intel_weight = 1.0
    intel_bias = None
    try:
        intel = market_intel_results.get(symbol) if isinstance(market_intel_results, dict) else None
        if intel:
            intel_bias = intel.get("bias")
            intel_structure = intel.get("structure")
            if intel_bias == base_direction and intel_structure in ("HH_HL", "LL_LH"):
                intel_weight = 1.25
            elif intel_bias == base_direction:
                intel_weight = 1.15
            elif intel_bias in ("LONG", "SHORT") and intel_bias != base_direction:
                intel_weight = 0.7
    except Exception:
        pass

    try:
        sentiment_data = sentiment_analyzer.get_reddit_sentiment(asset_name)
        sentiment_score = sentiment_data.get('sentiment_score', 0)
        
        if base_direction == "LONG" and sentiment_score > 0.1:
            base_adj = min(10, int(sentiment_score * 30))
            adjustment = int(base_adj * intel_weight)
            total_adjustment += adjustment
            adjustments.append(f"SENTIMENT_{sentiment_data.get('sentiment_label', 'BULLISH')}")
            confidence_factors.append(('SENTIMENT', min(1.0, abs(sentiment_score) * 2 * intel_weight)))
        elif base_direction == "SHORT" and sentiment_score < -0.1:
            base_adj = min(10, int(abs(sentiment_score) * 30))
            adjustment = int(base_adj * intel_weight)
            total_adjustment += adjustment
            adjustments.append(f"SENTIMENT_{sentiment_data.get('sentiment_label', 'BEARISH')}")
            confidence_factors.append(('SENTIMENT', min(1.0, abs(sentiment_score) * 2 * intel_weight)))
        elif abs(sentiment_score) > 0.2:
            if (base_direction == "LONG" and sentiment_score < -0.2) or \
               (base_direction == "SHORT" and sentiment_score > 0.2):
                conflict_penalty = 6 if intel_weight >= 1.15 else 4
                total_adjustment -= conflict_penalty
                adjustments.append("SENTIMENT_CONFLICT")
    except Exception as e:
        print(f"Sentiment integration error: {e}")
    
    try:
        onchain_data = onchain_analytics.get_comprehensive_analysis(asset_name)
        onchain_signal = onchain_data.get('overall_signal', 'NEUTRAL')
        onchain_score = onchain_data.get('onchain_score', 0)
        
        if base_direction == "LONG" and onchain_signal == "BULLISH":
            base_adj = min(15, abs(onchain_score) // 2)
            adjustment = int(base_adj * intel_weight)
            total_adjustment += adjustment
            adjustments.append("WHALE_ACCUMULATION")
            confidence_factors.append(('ONCHAIN', min(1.0, abs(onchain_score) / 35 * intel_weight)))
        elif base_direction == "SHORT" and onchain_signal == "BEARISH":
            base_adj = min(15, abs(onchain_score) // 2)
            adjustment = int(base_adj * intel_weight)
            total_adjustment += adjustment
            adjustments.append("WHALE_DISTRIBUTION")
            confidence_factors.append(('ONCHAIN', min(1.0, abs(onchain_score) / 35 * intel_weight)))
        elif onchain_signal != 'NEUTRAL':
            conflict_penalty = 6 if intel_weight >= 1.15 else 4
            total_adjustment -= conflict_penalty
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
def order_flow_filter(df, direction: str, confluence_score: int = 0, relax_level: int = 0) -> tuple:
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
        range_mult = 1.2
        volume_mult = 1.0
        if confluence_score >= 60:
            range_mult = 1.35
            volume_mult = 0.85
        if relax_level >= 2:
            range_mult = max(range_mult, 1.3)
            volume_mult = min(volume_mult, 0.9)
        fake_breakout = (
            range_candle > avg_range * range_mult and
            volume < avg_volume * volume_mult
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
            if (confluence_score >= 70 or relax_level >= 2) and rejection and (follow_through or absorption):
                return True, "OF_FAKE_BREAKOUT_BYPASS"
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
    - LONG: 1H RSI buvo <= 40 per paskutines 6 valandas (oversold zona)
    - SHORT: 1H RSI buvo >= 60 per paskutines 6 valandas (overbought zona)
    
    Returns:
        (bool, str): (ar_praėjo_filtrą, paaiškinimas)
    """
    # RSI filter disabled
    return True, "RSI_DISABLED"
    
    rsi_1h = calc_rsi(df_1h['close'])
    if rsi_1h is None or len(rsi_1h) < lookback:
        return True, "1H_RSI_CALC_ERROR"
    
    rsi_window = rsi_1h.iloc[-lookback:]
    min_rsi = rsi_window.min()
    max_rsi = rsi_window.max()
    current_rsi = rsi_1h.iloc[-1]
    bear_market_active = bool(
        'bear_engine' in globals()
        and getattr(bear_engine, 'detector', None)
        and bear_engine.detector.is_bear_market
    )
    
    long_min, short_max = get_adaptive_rsi_prefilter_thresholds()
    
    if direction == "LONG":
        was_oversold = min_rsi <= long_min
        if was_oversold:
            return True, f"1H_RSI_OVERSOLD_{min_rsi:.0f}"
        else:
            return False, f"1H_RSI_NOT_OVERSOLD (min={min_rsi:.0f}, need<={long_min:.0f})"
    
    elif direction == "SHORT":
        if bear_market_active:
            print(f"⚠️ Bear market: Bypassing RSI check for SHORT (RSI: {current_rsi:.0f})")
            return True, "1H_RSI_SHORT_BYPASS"
        was_overbought = max_rsi >= short_max
        if was_overbought:
            return True, f"1H_RSI_OVERBOUGHT_{max_rsi:.0f}"
        return False, f"1H_RSI_NOT_OVERBOUGHT (max={max_rsi:.0f}, need>={short_max:.0f})"
    
    return True, "1H_RSI_NEUTRAL"


def check_directional_rsi_protection(rsi_value: float, direction: str, is_bear_market: bool = False):
    """
    Direction-aware RSI protection.
    """
    if direction == "LONG":
        if rsi_value <= 30:
            return False, "EXTREME_OVERSOLD_WAIT_BULLISH_REVERSAL"
        return True, "RSI_OK_LONG"
    if direction == "SHORT":
        if is_bear_market:
            if rsi_value > 40:
                return False, "EXTREME_OVERBOUGHT_WAIT_BEARISH_REJECTION"
            return True, "RSI_OK_BEAR_SHORT"
        if rsi_value < 68:
            return False, "EXTREME_OVERBOUGHT_WAIT_BEARISH_REJECTION"
        return True, "RSI_OK_BULL_SHORT"
    return True, "RSI_OK"


def evaluate_1h_rsi(rsi_1h: float, direction: str):
    """
    Soft RSI filter for DAY / CASHFLOW trading.
    Returns: (allowed: bool, penalty: int, reason: str)
    penalty = score reduction (0 = none)
    """
    return True, 0, "RSI_DISABLED"


def evaluate_short_rsi_flow(rsi: float, trend: str):
    """
    CASHFLOW / TREND SHORT RSI logic.
    Returns (allowed: bool, reason: str | None)
    """
    if trend not in ("BEAR", "STRONG_BEAR"):
        return True, None

    if rsi < 20:
        return False, "RSI_EXHAUSTION_BLOCK (<20)"

    if 25 <= rsi < 35:
        return True, "RSI_TREND_CONTINUATION_CAUTION (25-35)"

    if 35 <= rsi <= 60:
        return True, "RSI_TREND_CONTINUATION_OK (35-60)"

    if rsi > 60:
        return True, "RSI_BEARISH_REJECTION_ZONE (>60)"

    return True, None


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
# STRATEGY DETERMINATION
# ================================
def determine_strategy_name(signals: list, trend: str, is_countertrend: bool = False) -> str:
    """
    Determine strategy name from signal characteristics
    
    Args:
        signals: List of signal strings
        trend: Current trend (BULL, BEAR, etc.)
        is_countertrend: Whether this is a counter-trend trade
    
    Returns:
        Strategy name from STRATEGY_LIST
    """
    signals_str = " ".join(signals).upper()
    
    # COUNTER_TREND: Explicit counter-trend signals
    if is_countertrend or "COUNTER_TREND" in signals_str or "QUANT_COUNTER_TREND" in signals_str:
        return "COUNTER_TREND"
    
    # BREAKOUT: Structure breaks, momentum reversals, liquidity sweeps
    if any(sig in signals_str for sig in ["STRUCTURE_BULL", "STRUCTURE_BEAR", "MOMENTUM_REVERSAL", 
                                          "LIQUIDITY_SWEEP", "BREAKOUT", "SFP"]):
        return "BREAKOUT"
    
    # PULLBACK: Pullback completion signals
    if "PULLBACK" in signals_str or "PULLBACK_COMPLETE" in signals_str:
        return "PULLBACK"
    
    # SCALP_REBOUND: Rebound from oversold/overbought, RSI extremes
    if any(sig in signals_str for sig in ["REBOUND", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SCALP"]):
        return "SCALP_REBOUND"
    
    # TREND_CONTINUATION: Default for trend-following trades
    return "TREND_CONTINUATION"


# ================================
# ENTRY SIGNAL GENERATION (15min)
# ================================
def generate_entry_signal(
    symbol,
    df,
    trend,
    df_htf=None,
    macro_data=None,
    df_1h=None,
    df_daily=None,
    df_weekly=None,
    quant_bias=0,
    demand_zone=None,
    supply_zone=None,
    in_demand_zone=False,
    in_supply_zone=False,
    near_demand_zone=False,
    near_supply_zone=False,
):
    if df is None or len(df) < 50:
        return None

    if not QUANT_ENABLED:
        quant_bias = 0
    
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
    
    # v8.9.3: QUANT BIAS INTEGRATION (disabled for direction in Phase 1)
    if QUANT_DIRECTION_ENABLED:
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
    momentum_reversal_short = price_change_5 < -0.003  # At least -0.3% drop (cashflow)
    
    # MACD confirmation: check if MACD is bearish
    from ta.trend import MACD as MACD_Indicator
    macd_indicator = MACD_Indicator(close)
    macd_line = macd_indicator.macd().iloc[-1]
    macd_signal = macd_indicator.macd_signal().iloc[-1]
    macd_bearish = macd_line < macd_signal  # MACD below signal = bearish
    
    # For SHORT: require full alignment (4H+1H BEAR) and confirm via MACD or momentum
    short_confirmed = short_candle_ok and full_bear_alignment and (macd_bearish or momentum_reversal_short)
    
    # HTF market state + location context (global decision tree)
    htf_supply_zone = None
    htf_demand_zone = None
    in_supply_zone = False
    in_demand_zone = False
    location_state = "MID_RANGE"
    zone_accepted = False
    next_candle_bullish = False
    zone_source = None
    if df_htf is not None and len(df_htf) >= 20:
        try:
            atr_htf = calc_atr(df_htf).iloc[-1]
            htf_blocks = OrderBlocks.detect_htf_order_blocks(df_htf, atr_htf, impulse_mult=ZONE_IMPULSE_MULT)
            for ob in htf_blocks:
                zone_type = OrderBlocks.classify_zone(ob)
                if ob.contains_price(current_price):
                    if zone_type == "SUPPLY":
                        in_supply_zone = True
                        htf_supply_zone = ob
                        htf_supply_zone.atr = atr_htf
                    if zone_type == "DEMAND":
                        in_demand_zone = True
                        htf_demand_zone = ob
                        htf_demand_zone.atr = atr_htf
            if htf_supply_zone:
                if current_price > htf_supply_zone.top:
                    location_state = "ABOVE_SUPPLY"
                else:
                    location_state = "AT_SUPPLY"
                zone_source = "HTF"
            elif htf_demand_zone:
                if current_price < htf_demand_zone.bottom:
                    location_state = "BELOW_DEMAND"
                else:
                    location_state = "AT_DEMAND"
                zone_source = "HTF"
        except Exception:
            pass
    if zone_source is None and df is not None and len(df) >= 20:
        try:
            atr_ltf = calc_atr(df).iloc[-1]
            ltf_blocks = OrderBlocks.detect_htf_order_blocks(df, atr_ltf, impulse_mult=ZONE_IMPULSE_MULT)
            ltf_supply = []
            ltf_demand = []
            for ob in ltf_blocks:
                zone_type = OrderBlocks.classify_zone(ob)
                if ob.contains_price(current_price):
                    if zone_type == "SUPPLY" and htf_supply_zone is None:
                        in_supply_zone = True
                        htf_supply_zone = ob
                        htf_supply_zone.atr = atr_ltf
                        zone_source = "LTF"
                    if zone_type == "DEMAND" and htf_demand_zone is None:
                        in_demand_zone = True
                        htf_demand_zone = ob
                        htf_demand_zone.atr = atr_ltf
                        zone_source = "LTF"
                if zone_type == "SUPPLY":
                    ltf_supply.append(ob)
                if zone_type == "DEMAND":
                    ltf_demand.append(ob)
            if zone_source is None and (ltf_supply or ltf_demand):
                def _zone_distance(z):
                    if z.contains_price(current_price):
                        return 0.0
                    return min(abs(current_price - z.top), abs(current_price - z.bottom))
                nearest_supply = min(ltf_supply, key=_zone_distance) if ltf_supply else None
                nearest_demand = min(ltf_demand, key=_zone_distance) if ltf_demand else None
                supply_dist = _zone_distance(nearest_supply) if nearest_supply else None
                demand_dist = _zone_distance(nearest_demand) if nearest_demand else None
                max_dist = atr_ltf * 3 if atr_ltf else None
                if max_dist and ((supply_dist is not None and supply_dist <= max_dist) or (demand_dist is not None and demand_dist <= max_dist)):
                    if supply_dist is not None and (demand_dist is None or supply_dist <= demand_dist):
                        htf_supply_zone = nearest_supply
                        htf_supply_zone.atr = atr_ltf
                        zone_source = "LTF"
                    elif demand_dist is not None:
                        htf_demand_zone = nearest_demand
                        htf_demand_zone.atr = atr_ltf
                        zone_source = "LTF"
            if zone_source == "LTF":
                if htf_supply_zone:
                    location_state = "AT_SUPPLY" if current_price <= htf_supply_zone.top else "ABOVE_SUPPLY"
                elif htf_demand_zone:
                    location_state = "AT_DEMAND" if current_price >= htf_demand_zone.bottom else "BELOW_DEMAND"
        except Exception:
            pass
    if location_state == "MID_RANGE" and (not in_supply_zone and not in_demand_zone):
        location_state = "MID_RANGE"

    structure_result_1h = analyze_market_structure(df_1h, lookback=50) if df_1h is not None else {}
    no_recent_bos = structure_result_1h.get("structure_break") is None
    htf_structure = structure_result_1h.get("structure", "NEUTRAL")
    if htf_structure == "BEARISH":
        htf_structure = "BEAR"
    elif htf_structure == "BULLISH":
        htf_structure = "BULL"
    else:
        htf_structure = "RANGE"
    structure_break = structure_result_1h.get("structure_break")
    if structure_break == "BULLISH_BREAK":
        htf_bos = "UP"
    elif structure_break == "BEARISH_BREAK":
        htf_bos = "DOWN"
    else:
        htf_bos = None
    market_state = detect_market_state(htf_structure, htf_bos)
    pullback_result = None
    if market_state == MarketState.STRONG_BEAR and df is not None and len(df) >= 5:
        last_swing_high = structure_result_1h.get("last_swing_high")
        last_swing_low = structure_result_1h.get("last_swing_low")
        if last_swing_high and last_swing_low:
            impulse_high = last_swing_high[1]
            impulse_low = last_swing_low[1]
            pullback_high = df["high"].iloc[-5:].max()
            if TRADE_MODE == "SWING":
                healthy_min, healthy_max, overextended_min = 38, 61, 70
            else:
                healthy_min, healthy_max, overextended_min = 30, 70, 70
            pullback_result = evaluate_pullback(
                impulse_high=impulse_high,
                impulse_low=impulse_low,
                pullback_high=pullback_high,
                direction="BEAR",
                healthy_min=healthy_min,
                healthy_max=healthy_max,
                overextended_min=overextended_min,
            )

    # 🔴 Strong bear disables ALL long signal generation
    if market_state == MarketState.STRONG_BEAR:
        long_score = -9999

    if htf_supply_zone and len(df) >= 2:
        zone_high = htf_supply_zone.top
        breakout_candle = df.iloc[-2]
        confirm_candle = df.iloc[-1]
        breakout_range = breakout_candle["high"] - breakout_candle["low"]
        breakout_body = abs(breakout_candle["close"] - breakout_candle["open"])
        breakout_body_pct = breakout_body / breakout_range if breakout_range > 0 else 0
        zone_accepted = (breakout_candle["close"] > zone_high) and (breakout_body_pct >= 0.6)
        next_candle_bullish = confirm_candle["close"] >= confirm_candle["open"]
    demand_breakout_ok = False
    next_candle_bearish = False
    if htf_demand_zone and len(df) >= 2:
        zone_low = htf_demand_zone.bottom
        breakout_candle = df.iloc[-2]
        confirm_candle = df.iloc[-1]
        breakout_range = breakout_candle["high"] - breakout_candle["low"]
        breakout_body = abs(breakout_candle["close"] - breakout_candle["open"])
        breakout_body_pct = breakout_body / breakout_range if breakout_range > 0 else 0
        demand_breakout_ok = (breakout_candle["close"] < zone_low) and (breakout_body_pct >= 0.6)
        next_candle_bearish = confirm_candle["close"] <= confirm_candle["open"]

    # HTF S/R context (Daily + Weekly)
    htf_ctx = None
    daily_breakout_ok = False
    weekly_breakout_ok = False
    if df_daily is not None and len(df_daily) >= 3:
        daily_high = df_daily["high"].iloc[-3:].max()
        daily_low = df_daily["low"].iloc[-3:].min()
        daily_atr = calc_atr(df_daily).iloc[-1] if len(df_daily) >= 15 else 0
        daily_buffer = daily_atr * 0.25
        daily_res_low = daily_high - daily_buffer
        daily_sup_high = daily_low + daily_buffer
        near_daily_res = daily_res_low <= current_price <= daily_high
        near_daily_sup = daily_low <= current_price <= daily_sup_high
    else:
        daily_high = daily_low = daily_buffer = 0
        near_daily_res = near_daily_sup = False
        daily_res_low = daily_sup_high = 0

    if df_weekly is not None and len(df_weekly) >= 1:
        weekly_high = df_weekly["high"].iloc[-1]
        weekly_low = df_weekly["low"].iloc[-1]
        weekly_atr = calc_atr(df_weekly).iloc[-1] if len(df_weekly) >= 15 else 0
        weekly_buffer = weekly_atr * 0.5
        weekly_res_low = weekly_high - weekly_buffer
        weekly_sup_high = weekly_low + weekly_buffer
        near_weekly_res = weekly_res_low <= current_price <= weekly_high
        near_weekly_sup = weekly_low <= current_price <= weekly_sup_high
    else:
        weekly_high = weekly_low = weekly_buffer = 0
        near_weekly_res = near_weekly_sup = False
        weekly_res_low = weekly_sup_high = 0

    nearest_res_dist = None
    nearest_sup_dist = None
    if near_weekly_res:
        nearest_res_dist = abs(weekly_high - current_price) / current_price * 100 if current_price else 0
    elif near_daily_res:
        nearest_res_dist = abs(daily_high - current_price) / current_price * 100 if current_price else 0
    if near_weekly_sup:
        nearest_sup_dist = abs(current_price - weekly_low) / current_price * 100 if current_price else 0
    elif near_daily_sup:
        nearest_sup_dist = abs(current_price - daily_low) / current_price * 100 if current_price else 0

    dominant_barrier = "NONE"
    if near_weekly_res or near_weekly_sup:
        dominant_barrier = "WEEKLY"
    elif near_daily_res or near_daily_sup:
        dominant_barrier = "DAILY"

    htf_ctx = HTFContext(
        near_daily_resistance=near_daily_res,
        near_daily_support=near_daily_sup,
        near_weekly_resistance=near_weekly_res,
        near_weekly_support=near_weekly_sup,
        distance_to_nearest_resistance=nearest_res_dist or 0,
        distance_to_nearest_support=nearest_sup_dist or 0,
        dominant_barrier=dominant_barrier,
    )

    if df is not None and len(df) >= 2:
        breakout_candle = df.iloc[-2]
        confirm_candle = df.iloc[-1]
        breakout_range = breakout_candle["high"] - breakout_candle["low"]
        breakout_body = abs(breakout_candle["close"] - breakout_candle["open"])
        breakout_body_pct = breakout_body / breakout_range if breakout_range > 0 else 0
        next_bullish = confirm_candle["close"] >= confirm_candle["open"]
        next_bearish = confirm_candle["close"] <= confirm_candle["open"]
        if near_daily_res:
            daily_breakout_ok = (breakout_candle["close"] > daily_high) and (breakout_body_pct >= 0.6) and next_bullish
        if near_weekly_res:
            weekly_breakout_ok = (breakout_candle["close"] > weekly_high) and (breakout_body_pct >= 0.6) and next_bullish
        if near_daily_sup:
            daily_breakout_ok = (breakout_candle["close"] < daily_low) and (breakout_body_pct >= 0.6) and next_bearish
        if near_weekly_sup:
            weekly_breakout_ok = (breakout_candle["close"] < weekly_low) and (breakout_body_pct >= 0.6) and next_bearish

    # v8.2: PULLBACK COMPLETION FILTER - wait for pullback to complete before entry
    pullback_ok_short, pullback_reason_short = pullback_complete_short(df, ema21)
    pullback_ok_long, pullback_reason_long = pullback_complete_long(df, ema21)

    rejection_result = None
    fake_breakout_result = None
    entry_delay_result = None
    if df is not None and len(df) >= 3:
        rejection_candle = df.iloc[-2]
        next_candle = df.iloc[-1]
        prev_candle = df.iloc[-3]
        into_zone = location_state in ("AT_SUPPLY", "AT_DEMAND")
        zone_high = None
        zone_low = None
        if location_state == "AT_SUPPLY" and htf_supply_zone is not None:
            zone_high = htf_supply_zone.top
            zone_low = htf_supply_zone.bottom
        if location_state == "AT_DEMAND" and htf_demand_zone is not None:
            zone_high = htf_demand_zone.top
            zone_low = htf_demand_zone.bottom
        volume_spike = False
        if df is not None and len(df) >= 20 and "volume" in df.columns:
            vol_avg = df["volume"].iloc[-20:].mean()
            volume_spike = df["volume"].iloc[-2] > (1.5 * vol_avg) if vol_avg else False
        momentum_loss = False
        if rsi is not None and len(rsi) >= 3:
            rsi_now = rsi.iloc[-2]
            rsi_prev = rsi.iloc[-3]
            if location_state == "AT_SUPPLY":
                momentum_loss = rsi_now < rsi_prev
            elif location_state == "AT_DEMAND":
                momentum_loss = rsi_now > rsi_prev
        rejection_context = {
            "direction": "SHORT" if location_state == "AT_SUPPLY" else "LONG" if location_state == "AT_DEMAND" else None,
            "location_valid": into_zone,
            "zone_high": zone_high,
            "zone_low": zone_low,
            "atr": atr_val,
            "volume_spike": volume_spike,
            "prev_candle_close": prev_candle["close"] if prev_candle is not None else None,
            "momentum_loss": momentum_loss,
            "htf_trend": trend,
            "mode": TRADE_MODE,
        }
        if rejection_context["direction"]:
            rejection_result = evaluate_rejection(
                rejection_candle,
                prev_candle,
                next_candle,
                rejection_context,
                rsi_series=rsi,
            )
            required_confirms = 2 if TRADE_MODE == "SWING" else 1
            entry_delay_result = update_entry_delay_state(
                symbol,
                rejection_context["direction"],
                rejection_candle,
                next_candle,
                required_confirms,
            )
        if location_state == "AT_SUPPLY" and htf_supply_zone is not None:
            fake_breakout_result = evaluate_fake_breakout(
                rejection_candle,
                next_candle,
                htf_supply_zone,
                "SHORT",
            )
        elif location_state == "AT_DEMAND" and htf_demand_zone is not None:
            fake_breakout_result = evaluate_fake_breakout(
                rejection_candle,
                next_candle,
                htf_demand_zone,
                "LONG",
            )

    # Market regime context
    consecutive_same_dir = 0
    if df is not None and len(df) >= 5:
        last_dirs = []
        for i in range(-5, 0):
            last_dirs.append("UP" if df["close"].iloc[i] >= df["open"].iloc[i] else "DOWN")
        consecutive_same_dir = 1
        for i in range(len(last_dirs) - 1, 0, -1):
            if last_dirs[i] == last_dirs[i - 1]:
                consecutive_same_dir += 1
            else:
                break

    atr_pct = (atr_val / current_price * 100) if current_price else 0
    impulse_body_atr = (abs(df["close"].iloc[-1] - df["open"].iloc[-1]) / atr_val) if atr_val else 0
    volume_spike = False
    if df is not None and len(df) >= 20:
        vol_avg = df["volume"].iloc[-20:].mean()
        volume_spike = df["volume"].iloc[-1] > (1.5 * vol_avg) if vol_avg else False
        midrange = (df["high"].iloc[-20:].max() + df["low"].iloc[-20:].min()) / 2
    else:
        midrange = current_price
    close_above_midrange = current_price > midrange
    close_below_midrange = current_price < midrange
    structure_broken = structure_result_1h.get("structure_break") is not None

    regime_ctx = DecisionRegimeContext(
        rsi=rsi_val,
        atr_pct=atr_pct,
        impulse_body_atr=impulse_body_atr,
        volume_spike=volume_spike,
        consecutive_same_dir=consecutive_same_dir,
        structure_broken=structure_broken,
        close_above_midrange=close_above_midrange,
        close_below_midrange=close_below_midrange,
    )
    signal_regime = detect_regime_ctx(regime_ctx) if regime_ctx is not None else None

    expansion_ctx = {
        "consecutive_candles": consecutive_same_dir,
        "pullback_depth_pct": max(0.0, (df["high"].iloc[-20:].max() - current_price) / max((df["high"].iloc[-20:].max() - df["low"].iloc[-20:].min()), 1e-9) * 100) if df is not None and len(df) >= 20 else 0,
        "atr_pct": atr_pct,
    }
    
    # v8.2: VOLUME CONFIRMATION
    volume_ok, volume_reason = check_volume_confirmation(df, "SHORT" if short_score > long_score else "LONG")
    
    # v8.2: VOLATILITY-ADJUSTED STOP LOSS
    vix_value = macro_state.get('vix_value', 20)
    atr_multiplier = get_volatility_adjusted_atr_multiplier(df_htf, vix_value)
    sl_distance = atr_val * atr_multiplier  # Dynamic based on volatility
    
    # For LONG in BEAR market: allow if RSI <= RSI_OVERSOLD (Quant disabled for direction)
    long_in_bear_ok = rsi_val <= RSI_OVERSOLD
    quant_counter_trend = QUANT_DIRECTION_ENABLED and quant_bias >= 15  # v8.9.3: Quant-confirmed counter-trend

    # Counter-trend extras: allow with strong confluence or support bounce
    support_bounce_ct = False
    confluence_score_long = 0
    confluence_ct = False
    support_ct = False
    if trend in ["STRONG_BEAR", "BEAR"]:
        support_bounce_ct, _support_dist = detect_support_bounce(
            df_1h, current_price, relax_level=get_adaptive_relax_level()
        )
        confluence_score_long, _bypass_long, _reasons_long = check_multi_indicator_confluence(
            df, df_1h, df_htf, "LONG"
        )
        support_ct = support_bounce_ct and long_candle_ok and rsi_val <= 50
        confluence_ct = confluence_score_long >= 65 and long_candle_ok and rsi_val <= 45
    ct_override_allowed = support_ct or confluence_ct
    allow_aggressive_ct = QUANT_DIRECTION_ENABLED and quant_counter_trend and quant_bias >= 20

    # 🟢 Contrarian LONG only in RANGE with no recent BOS
    contrarian_allowed = (market_state == MarketState.RANGE) and no_recent_bos
    if TRADE_MODE == "CASHFLOW":
        contrarian_allowed = False
    if not contrarian_allowed:
        quant_counter_trend = False
        long_in_bear_ok = False
        ct_override_allowed = False
    
    # v8.9.4: Check if counter-trend is auto-disabled due to poor performance
    ct_stats = get_counter_trend_stats()
    if ct_stats["ct_disabled"]:
        quant_counter_trend = False  # Disable counter-trend signals
        if QUANT_DIRECTION_ENABLED and quant_bias >= 15:
            print(f"  ⚠️ {symbol}: Counter-trend DISABLED (win rate: {ct_stats['ct_win_rate']}%, {ct_stats['ct_total']} trades)")
    
    # v8.9.6: Counter-trend requires MANDATORY Candle Reversal pattern (The Rumers style)
    # Both quant_counter_trend AND long_in_bear_ok require candle reversal confirmation
    # RSI must be <= 40 (stricter) AND bullish candle reversal pattern must be present
    has_candle_reversal = False
    candle_rev_pattern = None
    
    # ================================
    # STRATEGY HEALTH CHECK (FIRST - BEFORE REBOUND REFINER)
    # ================================
    # Determine preliminary strategy for health check (rebound is for LONG in BEAR = COUNTER_TREND or SCALP_REBOUND)
    preliminary_strategy = None
    rebound_allowed = False
    size_multiplier = 1.0
    health_status = None  # Store health status for additional safety check
    
    if trend in ["BEAR", "STRONG_BEAR"]:
        # Rebound refiner applies to LONG signals in BEAR markets
        # Strategy would be COUNTER_TREND or SCALP_REBOUND depending on signals
        if long_in_bear_ok or quant_counter_trend:
            preliminary_strategy = "COUNTER_TREND"
        elif rsi_val <= 30:
            preliminary_strategy = "SCALP_REBOUND"
        else:
            preliminary_strategy = "COUNTER_TREND"  # Default for LONG in BEAR
        
        # Check strategy health FIRST
        health = strategy_health_engine.evaluate_strategy(preliminary_strategy)
        health_status = health.status  # Store for additional safety check
        
        if health.status == "DISABLED":
            # Strategy is disabled - block signal generation entirely
            print(f"  🚫 {symbol}: Strategy {preliminary_strategy} is DISABLED - {health.reason}")
            return None  # Block signal generation
        elif health.status == "WARNING":
            size_multiplier = 0.5
            rebound_allowed = False  # WARNING = no rebound allowed
            print(f"  ⚠️ {symbol}: Strategy {preliminary_strategy} is WARNING - Rebound disabled, size reduced 50%")
        else:
            size_multiplier = 1.0
            rebound_allowed = True
        
        # Additional conditions to disable rebound
        if circuit_state.get("consecutive_losses", 0) >= 2:
            rebound_allowed = False
            print(f"  ⚠️ {symbol}: Rebound disabled - {circuit_state.get('consecutive_losses', 0)} consecutive losses")
        
        # Check daily limit proximity
        balance = get_available_balance()
        capital = balance.get("total_usd", 0)
        if capital > 0:
            daily_limit = capital * (DAILY_LOSS_LIMIT_PCT / 100)
            if auto_trading_state.get("daily_pnl", 0) < -0.5 * daily_limit:
                rebound_allowed = False
                print(f"  ⚠️ {symbol}: Rebound disabled - Daily P&L near limit (${auto_trading_state.get('daily_pnl', 0):.2f} < -${0.5 * daily_limit:.2f})")
    
    # ================================
    # REBOUND ENTRY REFINER (ONLY IF ALLOWED)
    # ================================
    has_bullish_divergence = detect_rsi_divergence(df_1h, direction="LONG") if df_1h is not None else False
    rebound_refiner_result = None
    atr_ratio = 1.0
    rebound_score_boost = 0
    rebound_rr_improvement = 1.0
    
    if trend in ["BEAR", "STRONG_BEAR"] and rebound_allowed:
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
        
        # Get market regime
        MARKET_REGIME = market_regime_state.get("regime", "NEUTRAL")
        
        rebound_ctx = ReboundRefinerContext(
            market_regime=MARKET_REGIME,
            rsi=rsi_val,
            atr_ratio=atr_ratio,
            has_bullish_divergence=has_bullish_divergence,
            candle_reversal=long_candle_ok,
            bullish_candle_close=bullish_candle_close
        )
        rebound_refiner_result = evaluate_rebound_refiner(rebound_ctx)
        
        # Additional safety check: If Rebound is active AND Strategy Health = WARNING → Don't allow rebound boost
        if rebound_refiner_result and rebound_refiner_result.active:
            if health_status == "WARNING":
                print(f"  ⚠️ {symbol}: REBOUND REFINER blocked - Strategy Health is WARNING (safety check)")
                rebound_score_boost = 0
                rebound_rr_improvement = 1.0
            else:
                rebound_score_boost = rebound_refiner_result.score_boost
                rebound_rr_improvement = rebound_refiner_result.rr_improvement
                print(f"  📈 {symbol}: REBOUND REFINER +{rebound_score_boost}pts, R:R×{rebound_rr_improvement:.2f} ({rebound_refiner_result.reason})")
                long_signals.append(f"REBOUND_BOOST+{rebound_score_boost}")
    
    # Legacy compatibility (empty result for non-BEAR)
    scalp_rebound_result = None
    
    # v8.9.6: Check for candle reversal pattern for ANY counter-trend LONG
    counter_trend_long_mode = long_score >= short_score
    is_at_key_zone = False
    key_zone_type = None
    if counter_trend_long_mode:
        ct_rsi_limit = 50 if ct_override_allowed else 40
        counter_trend_possible = (
            (quant_counter_trend or long_in_bear_ok or ct_override_allowed)
            and rsi_val <= ct_rsi_limit
        )
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
            if quant_counter_trend and not allow_aggressive_ct:
                quant_counter_trend = False
                print(f"  ⚠️ {symbol}: Counter-trend blocked - no bullish reversal candle (RSI={rsi_val:.0f})")
            if long_in_bear_ok:
                long_in_bear_ok = False  # v8.9.6: Also block extreme oversold without reversal
                print(f"  ⚠️ {symbol}: Counter-trend blocked - no bullish reversal candle (RSI={rsi_val:.0f})")
            if ct_override_allowed:
                ct_override_allowed = False
                print(f"  ⚠️ {symbol}: Counter-trend blocked - no bullish reversal candle (RSI={rsi_val:.0f})")
        
        # v8.9.22: OB/FVG ZONE CHECK - Counter-trend MUST be at key zone
        # Professional rules: "Entry must be at Order Block or FVG zone"
        if has_candle_reversal and (long_in_bear_ok or (quant_counter_trend and not allow_aggressive_ct) or ct_override_allowed):
            zone_check = OrderBlocks.is_price_at_key_zone(df, 'LONG', tolerance_pct=0.8)
            is_at_key_zone = zone_check.get('is_at_zone', False)
            key_zone_type = zone_check.get('zone_type')
            
            if not is_at_key_zone:
                if quant_counter_trend and not allow_aggressive_ct:
                    quant_counter_trend = False
                    print(f"  ⚠️ {symbol}: Counter-trend blocked - not at OB/FVG zone")
                if long_in_bear_ok:
                    long_in_bear_ok = False
                    print(f"  ⚠️ {symbol}: Counter-trend blocked - not at OB/FVG zone")
                if ct_override_allowed:
                    ct_override_allowed = False
                    print(f"  ⚠️ {symbol}: Counter-trend blocked - not at OB/FVG zone")
    
    # v8.9.22: 4H CHoCH CONFIRMATION - Counter-trend MUST show structure change on 4H
    # Professional rules: "4H timeframe must confirm potential reversal (CHoCH)"
    # FAIL-SAFE: On any error, block counter-trend (don't allow without confirmation)
    has_4h_choch = False
    if is_at_key_zone and has_candle_reversal and (long_in_bear_ok or (quant_counter_trend and not allow_aggressive_ct) or ct_override_allowed):
        try:
            df_4h_check = fetch_ohlcv(symbol, '4h', limit=100)
            if df_4h_check is not None and len(df_4h_check) >= 50:
                choch_result = MarketStructure.detect_choch_4h(df_4h_check)
                has_4h_choch = choch_result.get('bullish_choch', False)
                
                if not has_4h_choch:
                    if quant_counter_trend and not allow_aggressive_ct:
                        quant_counter_trend = False
                        print(f"  ⚠️ {symbol}: Counter-trend blocked - no 4H CHoCH confirmation")
                    if long_in_bear_ok:
                        long_in_bear_ok = False
                        print(f"  ⚠️ {symbol}: Counter-trend blocked - no 4H CHoCH confirmation")
                else:
                    print(f"  ✅ {symbol}: 4H CHoCH confirmed for counter-trend LONG")
            else:
                # FAIL-SAFE: Insufficient data = block counter-trend
                if quant_counter_trend and not allow_aggressive_ct:
                    quant_counter_trend = False
                if long_in_bear_ok:
                    long_in_bear_ok = False
                print(f"  ⚠️ {symbol}: Counter-trend blocked - insufficient 4H data for CHoCH check")
        except Exception as e:
            # FAIL-SAFE: Any error = block counter-trend (never allow without CHoCH confirmation)
            if quant_counter_trend and not allow_aggressive_ct:
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
        if trend in ["STRONG_BULL", "BULL", "NEUTRAL"] or (trend in ["STRONG_BEAR", "BEAR"] and (long_in_bear_ok or quant_counter_trend or ct_override_allowed)):
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
                if support_ct:
                    base_signals.append("SUPPORT_BOUNCE_CT")
                if confluence_ct:
                    base_signals.append("CONFLUENCE_CT")
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

    zone_resolution_state = None
    zone_resolution_reason = None
    zone_context_type = None
    zone_resolution_closes = ""
    zone_resolution_body_pct = 0.0

    if base_direction:
        # Decision engine (market state -> direction gate -> location -> breakout -> indicators)
        if location_state == "AT_SUPPLY":
            location_enum = Location.AT_SUPPLY
        elif location_state == "AT_DEMAND":
            location_enum = Location.AT_DEMAND
        elif location_state == "ABOVE_SUPPLY":
            location_enum = Location.ABOVE_SUPPLY
        elif location_state == "BELOW_DEMAND":
            location_enum = Location.BELOW_DEMAND
        else:
            location_enum = Location.MID_RANGE

        ema_alignment = "BULL" if ema9 > ema21 else "BEAR"
        indicator_score_value = indicator_score(rsi_val, ema_alignment, base_direction)
        indicator_bias = None
        if base_direction == "LONG" and ema_alignment == "BEAR":
            indicator_bias = "BLOCK_LONG"
        if base_direction == "SHORT" and ema_alignment == "BULL":
            indicator_bias = "BLOCK_SHORT"
        pullback_state = None
        if pullback_result and pullback_result.state == "HEALTHY_PULLBACK":
            pullback_state = "ENTER"
        recent_impulse = pullback_impulse_states.get(symbol, "NO_IMPULSE") == "HOT"
        breakout_ok = False
        if base_direction == "LONG" and location_enum in (Location.AT_SUPPLY, Location.ABOVE_SUPPLY) and htf_supply_zone is not None and len(df) >= 2:
            breakout_ok = zone_accepted and next_candle_bullish
        if base_direction == "SHORT" and location_enum in (Location.AT_DEMAND, Location.BELOW_DEMAND) and htf_demand_zone is not None and len(df) >= 2:
            breakout_ok = demand_breakout_ok and next_candle_bearish

        # HTF S/R gate before decision engine
        if htf_ctx:
            htf_allowed, htf_reason, htf_size_mult, htf_conf_mult = evaluate_htf_sr_gate(
                TRADE_MODE, market_state, base_direction, htf_ctx, daily_breakout_ok, weekly_breakout_ok
            )
            if not htf_allowed:
                print(
                    f"[HTF_SR] TF={htf_ctx.dominant_barrier} Action=BLOCK_{base_direction} "
                    f"Reason={htf_reason} DistR={htf_ctx.distance_to_nearest_resistance:.2f}% "
                    f"DistS={htf_ctx.distance_to_nearest_support:.2f}%"
                )
                record_block("HTF_SR")
                return None
        else:
            htf_size_mult = 1.0
            htf_conf_mult = 1.0

        zone_for_signal = None
        if base_direction == "LONG" and demand_zone and (in_demand_zone or near_demand_zone):
            zone_for_signal = demand_zone
            zone_context_type = "DEMAND"
        elif base_direction == "SHORT" and supply_zone and (in_supply_zone or near_supply_zone):
            zone_for_signal = supply_zone
            zone_context_type = "SUPPLY"

        zone_engine = ZoneResolutionEngine(
            min_body_pct=ZONE_RESOLUTION_RELAXED_BODY_PCT,
            required_closes=ZONE_RESOLUTION_REQUIRED_CLOSES,
        )
        zone_state = "OUTSIDE"
        zone_confidence = 0
        zone_confirmation = False
        zone_type_value = None
        approach_direction = None
        prev_close = df["close"].iloc[-2] if df is not None and len(df) >= 2 else current_price
        if current_price is not None and prev_close is not None:
            approach_direction = "UP" if current_price >= prev_close else "DOWN"
        if zone_for_signal is not None and ((df_htf is not None and len(df_htf) >= 5) or (df is not None and len(df) >= 5)):
            data_df = df if zone_source == "LTF" else df_htf
            atr_for_score = None
            if data_df is not None and len(data_df) >= 15:
                atr_for_score = calc_atr(data_df).iloc[-1]
            zone_age_hours = 0.0
            impulse_atr = 0.0
            test_count = 0
            wick_only = False
            zone_index = getattr(zone_for_signal, "index", None)
            zone_type_value = zone_context_type
            if data_df is not None and zone_index is not None and zone_index < len(data_df):
                hours_per_candle = 0.25 if zone_source == "LTF" else 4
                zone_age_hours = float(max(0, (len(data_df) - 1 - zone_index) * hours_per_candle))
                if atr_for_score:
                    if zone_context_type == "DEMAND" and zone_index + 1 < len(data_df):
                        impulse = data_df["high"].iloc[zone_index + 1] - data_df["low"].iloc[zone_index]
                        impulse_atr = impulse / atr_for_score
                    if zone_context_type == "SUPPLY" and zone_index + 1 < len(data_df):
                        impulse = data_df["high"].iloc[zone_index] - data_df["low"].iloc[zone_index + 1]
                        impulse_atr = impulse / atr_for_score
                closes = data_df["close"].iloc[zone_index + 1 :]
                test_count = int(((closes >= zone_for_signal.bottom) & (closes <= zone_for_signal.top)).sum())

            zone_state = "OUTSIDE"
            if zone_context_type == "SUPPLY":
                if in_supply_zone:
                    zone_state = "INSIDE"
                elif atr_for_score:
                    buffer_val = 0.25 * atr_for_score
                    if (zone_for_signal.bottom - buffer_val) <= current_price <= (zone_for_signal.top + buffer_val):
                        zone_state = "NEAR"
            if zone_context_type == "DEMAND":
                if in_demand_zone:
                    zone_state = "INSIDE"
                elif atr_for_score:
                    buffer_val = 0.25 * atr_for_score
                    if (zone_for_signal.bottom - buffer_val) <= current_price <= (zone_for_signal.top + buffer_val):
                        zone_state = "NEAR"

            zone_confidence = calculate_zone_confidence(
                ZoneConfidenceContext(
                    timeframe="15m" if zone_source == "LTF" else "4h",
                    age_hours=zone_age_hours,
                    impulse_atr=impulse_atr,
                    test_count=test_count,
                    wick_only=wick_only,
                )
            )
        if zone_for_signal is not None and len(df) >= (zone_engine.required_closes + 1):
            closed_df = df.iloc[:-1]
            last_n_df = closed_df.tail(zone_engine.required_closes)
            candles_15m = [
                Candle(
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
                for row in last_n_df.to_dict("records")
            ]
            zone_type = ZoneType.DEMAND if zone_context_type == "DEMAND" else ZoneType.SUPPLY
            active_zone = Zone(
                low=float(zone_for_signal.bottom),
                high=float(zone_for_signal.top),
                zone_type=zone_type,
            )
            zone_result = zone_engine.resolve(candles_15m, active_zone)
            zone_resolution_state = zone_result.state
            zone_resolution_reason = zone_result.reason
            zone_confirmation = zone_resolution_state in (
                ZoneResolutionState.CONFIRMED_BREAK,
                ZoneResolutionState.CONFIRMED_REJECTION,
            )
            if candles_15m:
                zone_resolution_body_pct = candles_15m[-1].body_pct
                confirmed_closes = 0
                for c in candles_15m:
                    if zone_type == ZoneType.SUPPLY:
                        if c.close > active_zone.high and c.body_pct >= ZONE_RESOLUTION_RELAXED_BODY_PCT:
                            confirmed_closes += 1
                    if zone_type == ZoneType.DEMAND:
                        if c.close < active_zone.low and c.body_pct >= ZONE_RESOLUTION_RELAXED_BODY_PCT:
                            confirmed_closes += 1
                zone_resolution_closes = f"{confirmed_closes}/{zone_engine.required_closes}"
            print(
                f"  → ZONE_RESOLUTION: {zone_resolution_state.value if zone_resolution_state else 'NONE'} | "
                f"{zone_resolution_reason} | CLOSES={zone_resolution_closes}"
            )

        accepted, decision_reason, decision_size_mult, decision_conf_mult, decision_tp_mod = evaluate_signal(
            market_state=market_state,
            location=location_enum,
            signal_direction=base_direction,
            breakout_ok=breakout_ok,
            indicator_score_value=indicator_score_value,
            indicator_bias=indicator_bias,
            rsi_value=rsi_val,
            pullback_state=pullback_state,
            recent_impulse=recent_impulse,
            pullback_result=pullback_result,
            rejection_result=rejection_result,
            fake_breakout_result=fake_breakout_result,
            entry_delay_result=entry_delay_result,
            zone_resolution_state=zone_resolution_state,
            zone_state=zone_state,
            zone_confidence=zone_confidence,
            zone_confirmation=zone_confirmation,
            zone_type=zone_type_value,
            approach_direction=approach_direction,
            mode=TRADE_MODE,
            regime_ctx=regime_ctx,
            expansion_ctx=expansion_ctx,
        )
        if pullback_result:
            print(
                f"  → PULLBACK_STATE: {pullback_result.state} "
                f"({pullback_result.retrace_pct:.1f}%) | {pullback_result.reason}"
            )
        if fake_breakout_result:
            print(
                f"  → FAKE_BREAKOUT: {fake_breakout_result.score} | "
                f"{fake_breakout_result.reason} ({fake_breakout_result.details})"
            )
        if rejection_result:
            print(
                f"  → REJECTION_SCORE: {rejection_result.score} | "
                f"{rejection_result.reason} ({rejection_result.details})"
            )
        if entry_delay_result:
            print(
                f"  → ENTRY_DELAY: {entry_delay_result.state} | {entry_delay_result.reason}"
            )
        if not accepted:
            log_decision(
                symbol,
                base_direction,
                market_state,
                decision_reason,
                zone_resolution=zone_resolution_state,
                zone_reason=zone_resolution_reason,
                zone_state=zone_state,
                zone_confidence=zone_confidence,
                zone_confirmation=zone_confirmation,
                zone_source=zone_source,
                approach_direction=approach_direction,
                indicator_score=indicator_score_value,
                indicator_bias=indicator_bias,
            )
            record_block("DECISION_ENGINE")
            return None

        bear_market_active = bool(
            'bear_engine' in globals()
            and getattr(bear_engine, 'detector', None)
            and bear_engine.detector.is_bear_market
        )
        rsi_1h_series = calc_rsi(df_1h['close']) if df_1h is not None else None
        rsi_1h = rsi_1h_series.iloc[-1] if rsi_1h_series is not None and len(rsi_1h_series) > 0 else rsi_val
        allowed, rsi_penalty, rsi_reason = evaluate_1h_rsi(rsi_1h, base_direction)
        if not allowed:
            print(f"  → ⏳ WAIT: RSI filter ({rsi_reason})")
            log_decision(
                symbol,
                base_direction,
                market_state,
                f"BLOCKED: {rsi_reason}",
                zone_resolution=zone_resolution_state,
                zone_reason=zone_resolution_reason,
                zone_state=zone_state,
                zone_confidence=zone_confidence,
                zone_confirmation=zone_confirmation,
                zone_source=zone_source,
                approach_direction=approach_direction,
                indicator_score=indicator_score_value,
                indicator_bias=indicator_bias,
            )
            record_block("RSI_SOFT_FILTER")
            return None
        if rsi_penalty < 0:
            base_score = max(0, base_score + rsi_penalty)
            base_signals.append(rsi_reason)

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
            # Allow bypass on strong confluence (cashflow mode)
            if confluence_score >= 45:
                skip_rsi_filter = True
                rsi_filter_reason = f"CONFLUENCE_BYPASS_{confluence_score}"
                print(f"  ✅ {symbol}: {base_direction} RSI bypassed (confluence={confluence_score}/55, rsi_1h={rsi_1h:.1f})")
            elif confluence_score >= 40:
                # Soft bypass with small penalty
                rsi_filter_reason = f"CONFLUENCE_PENALTY_{confluence_score}"
                base_score = max(0, base_score - 5)
                base_signals.append(rsi_filter_reason)
                print(f"  ⚠️ {symbol}: {base_direction} RSI soft-bypass (confluence={confluence_score}/55, rsi_1h={rsi_1h:.1f}, -5 score)")
            else:
                print(f"  ❌ {symbol}: {base_direction} blocked by 1H RSI filter - {rsi_filter_reason}")
                log_decision(
                    symbol,
                    base_direction,
                    market_state,
                    f"BLOCKED: {rsi_filter_reason}",
                    zone_resolution=zone_resolution_state,
                    zone_reason=zone_resolution_reason,
                    zone_state=zone_state,
                    zone_confidence=zone_confidence,
                    zone_confirmation=zone_confirmation,
                    zone_source=zone_source,
                    approach_direction=approach_direction,
                    indicator_score=indicator_score_value,
                    indicator_bias=indicator_bias,
                )
                record_block("RSI_1H_PREFILTER")
                return None
        if skip_rsi_filter and not rsi_filter_ok:
            if confluence_bypass:
                rsi_filter_reason = f"CONFLUENCE_BYPASS_{confluence_score}"  # Mark as confluence-bypassed
            else:
                rsi_filter_reason = "QUANT_RSI_BYPASS"  # Mark as quant-bypassed
        
        # v8.9.22: ORDER FLOW FILTER
        # Blocks fake breakouts (big range + low volume = trap)
        of_filter_ok, of_filter_reason = order_flow_filter(
            df,
            base_direction,
            confluence_score=confluence_score,
            relax_level=get_effective_relax_level("ORDER_FLOW")
        )
        if not of_filter_ok:
            print(f"  ❌ {symbol}: {base_direction} blocked by Order Flow filter - {of_filter_reason}")
            record_block("ORDER_FLOW")
            return None
        if of_filter_reason == "OF_FAKE_BREAKOUT_BYPASS":
            base_score = max(0, base_score - 3)
            base_signals.append(of_filter_reason)
        
        integrated = get_integrated_score(symbol, df_htf, df, base_direction, base_score)
        
        final_score = integrated['final_score']
        all_signals = base_signals + integrated['signals']

        # Market intel bias alignment
        intel = market_intel_results.get(symbol) if isinstance(market_intel_results, dict) else None
        if intel:
            intel_bias = intel.get("bias")
            if intel_bias == base_direction:
                final_score += 3
                all_signals.append("MI_BIAS_ALIGN")
            elif intel_bias in ("LONG", "SHORT") and intel_bias != base_direction:
                final_score -= 4
                all_signals.append("MI_BIAS_CONFLICT")
            intel_bos = intel.get("bos")
            intel_choch = intel.get("choch")
            if base_direction == "LONG" and intel_bos in ("BOS_UP", "CHOCH_UP"):
                final_score += 2
                all_signals.append(f"MI_{intel_bos}")
            elif base_direction == "SHORT" and intel_bos in ("BOS_DOWN", "CHOCH_DOWN"):
                final_score += 2
                all_signals.append(f"MI_{intel_bos}")
            elif intel_choch in ("CHOCH_UP", "CHOCH_DOWN") and intel_bias in ("LONG", "SHORT") and intel_bias != base_direction:
                final_score -= 2
                all_signals.append("MI_CHOCH_CONFLICT")
        
        # v8.9.19: Add confluence signals
        if confluence_bypass:
            all_signals.extend([f"CFN_{r}" for r in confluence_reasons[:3]])  # Add top 3 confluence reasons
            final_score += min(10, confluence_score // 10)  # Bonus points for confluence (max +10)
        
        # v8.8: Add 1H RSI filter signal
        if "1H_RSI" in rsi_filter_reason or "CONFLUENCE" in rsi_filter_reason or "QUANT" in rsi_filter_reason:
            all_signals.append(rsi_filter_reason.split(" ")[0])  # Add e.g. "1H_RSI_OVERSOLD_32" or "CONFLUENCE_BYPASS_60"
        
        # v8.9.22: Add Order Flow signal
        if of_filter_reason in ["OF_ABSORPTION_REJECTION", "OF_FOLLOW_THROUGH", "OF_FAKE_BREAKOUT_BYPASS"]:
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

        # RANGE + CASHFLOW: score-based (not hard block). Block only when zone_confidence < 40 AND score < 72.
        # Bypass: fake_breakout_score >= 4 → ALLOW even without zone
        if (market_state == MarketState.RANGE and TRADE_MODE == "CASHFLOW"):
            fake_bypass = fake_breakout_result and getattr(fake_breakout_result, "score", 0) >= 4
            if not fake_bypass and (zone_confidence or 0) < 40 and final_score < 72:
                print(f"  ❌ {symbol}: RANGE zone_conf<40 and score<72 (got {final_score}, zone_conf={zone_confidence})")
                log_decision(
                    symbol,
                    base_direction,
                    market_state,
                    "BLOCKED: RANGE_SCORE_TOO_LOW",
                    zone_resolution=zone_resolution_state,
                    zone_reason=zone_resolution_reason,
                    zone_state=zone_state,
                    zone_confidence=zone_confidence,
                    zone_confirmation=zone_confirmation,
                    zone_source=zone_source,
                    approach_direction=approach_direction,
                    indicator_score=indicator_score_value,
                    indicator_bias=indicator_bias,
                )
                record_block("RANGE_SCORE")
                return None

        if final_score >= effective_final_min:
            # v8.9.20: ConsolidationGuard - Block signals in range/consolidation
            is_consol, consol_reasons = is_consolidating(df, current_price, signal_data={
                'wave_score': integrated.get('wave_score', 50),
                'confluence_score': confluence_score
            })
            if is_consol:
                print(f"  🔄 {symbol}: CONSOLIDATION blocked - {', '.join(consol_reasons)}")
                record_block("CONSOLIDATION")
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
            
            # Determine strategy name
            is_countertrend_flag = trend in ["BEAR", "STRONG_BEAR"] if base_direction == "LONG" else False
            strategy_name = determine_strategy_name(all_signals, trend, is_countertrend_flag)

            # Support bounce (BEAR longs)
            support_bounce = False
            support_distance_pct = None
            has_bullish_reversal = bool(has_candle_reversal) if base_direction == "LONG" else False
            if base_direction == "LONG":
                support_bounce, support_distance_pct = detect_support_bounce(
                    df_1h, current_price, relax_level=get_adaptive_relax_level()
                )
                if support_bounce and has_bullish_reversal:
                    all_signals.append("SUPPORT_BOUNCE")
            
            # Build signal tags
            signal_tags = []
            if trend in ["STRONG_BULL", "BULL"]:
                signal_tags.append("TREND")
            elif trend in ["STRONG_BEAR", "BEAR"]:
                signal_tags.append("COUNTER_TREND")
            if rebound_refiner_result and rebound_refiner_result.active:
                signal_tags.append(f"REBOUND:{rebound_refiner_result.reason}")
            
            # Apply rebound rr improvement to atr_ratio (used by RR Engine)
            if rebound_rr_improvement > 1.0:
                atr_ratio = atr_ratio * rebound_rr_improvement  # Improve effective R:R
            
            # Apply decision engine size/confidence adjustments
            size_multiplier *= decision_size_mult * htf_size_mult
            confidence = max(0.0, min(1.0, confidence * decision_conf_mult * htf_conf_mult))

            # CASHFLOW: scale TP by zone (weak zone = lower expectations)
            zone_tp_mult = tp_multiplier_by_zone(zone_confidence, TRADE_MODE)
            tp_mult = min(decision_tp_mod, zone_tp_mult)
            if tp_mult < 1.0:
                if base_direction == "LONG":
                    tp1_sr = current_price + (tp1_sr - current_price) * tp_mult
                    tp2_distance *= tp_mult
                    tp3_distance *= tp_mult
                else:
                    tp1_sr = current_price - (current_price - tp1_sr) * tp_mult
                    tp2_distance *= tp_mult
                    tp3_distance *= tp_mult
                all_signals.append(f"TP_SCALED_{int(tp_mult*100)}")

        zone_resolution_telemetry = {
            "zone": zone_context_type or "",
            "status": "CONFIRMED"
            if zone_resolution_state in (ZoneResolutionState.CONFIRMED_BREAK, ZoneResolutionState.CONFIRMED_REJECTION)
            else "UNCONFIRMED",
            "closes": zone_resolution_closes,
            "body_pct": round(float(zone_resolution_body_pct), 4),
        }

        if base_direction == "LONG":
            holding_time = estimate_holding_time(
                signal_regime,
                atr_pct,
                (tp1_sr - current_price) / current_price * 100 if current_price else 0,
            )
            signal = {
                "symbol": symbol,
                "direction": "LONG",
                "score": final_score,
                "base_score": base_score,
                "signals": all_signals,
                "tags": signal_tags,  # Strategy tags for trade statistics
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
                "zone_resolution": zone_resolution_state.value if zone_resolution_state else "NONE",
                "zone_resolution_reason": zone_resolution_reason or "",
                "zone_context_type": zone_context_type or "",
                "signalDecision": {
                    "zone_resolution": zone_resolution_telemetry,
                },
                "quant_bias": integrated.get('quant_bias', 0),  # v8.9.2: For counter-trend
                "confluence_score": confluence_score,  # v8.9.19: Multi-indicator confluence
                "atr_ratio": atr_ratio,  # v8.9.23: For RR Engine (with rebound improvement applied)
                "is_countertrend": trend in ["BEAR", "STRONG_BEAR"],  # v8.9.23
                "rebound_boost": rebound_score_boost,  # v8.9.24: Entry refiner score boost
                "rebound_rr_mult": rebound_rr_improvement,  # v8.9.24: R:R improvement
                "strategy_name": strategy_name,  # Strategy identification
                "size_multiplier": size_multiplier,  # Strategy health size multiplier
                "holding_time": holding_time,
                "support_bounce": support_bounce,
                "support_distance_pct": support_distance_pct,
                "has_bullish_reversal": has_bullish_reversal,
                "time": datetime.now(timezone.utc),
            }
        else:
            holding_time = estimate_holding_time(
                signal_regime,
                atr_pct,
                (current_price - tp1_sr) / current_price * 100 if current_price else 0,
            )
            # SHORT signals - no rebound, but still need tags
            signal_tags = []
            if trend in ["STRONG_BEAR", "BEAR"]:
                signal_tags.append("TREND")

            signal = {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "score": final_score,
                    "base_score": base_score,
                    "signals": all_signals,
                    "tags": signal_tags,  # Strategy tags for trade statistics
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
                    "zone_resolution": zone_resolution_state.value if zone_resolution_state else "NONE",
                    "zone_resolution_reason": zone_resolution_reason or "",
                    "zone_context_type": zone_context_type or "",
                    "signalDecision": {
                        "zone_resolution": zone_resolution_telemetry,
                    },
                    "quant_bias": integrated.get('quant_bias', 0),  # v8.9.2: For counter-trend
                    "confluence_score": confluence_score,  # v8.9.19: Multi-indicator confluence
                    "atr_ratio": atr_ratio,  # v8.9.23: For RR Engine
                    "is_countertrend": False,  # SHORT is with-trend in BEAR
                    "rebound_boost": 0,  # v8.9.24: No boost for SHORT
                    "rebound_rr_mult": 1.0,  # v8.9.24: No improvement
                    "strategy_name": strategy_name,  # Strategy identification
                    "size_multiplier": size_multiplier,  # Apply decision size multiplier
                    "holding_time": holding_time,
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
    
    # Sprendimas: dinamika pagal rinkos sąlygas
    market_threshold = 3
    if atr_pct >= 1.6 or trend in ["STRONG_BULL", "STRONG_BEAR"] or any(sig in signals for sig in breakout_signals):
        market_threshold = 2  # Greitesnis įėjimas kai rinka juda
    
    if market_score >= market_threshold:
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

    holding_time = signal.get("holding_time")
    if isinstance(holding_time, dict) and holding_time.get("TP1") and holding_time.get("TP2"):
        hours_to_tp1 = max(1.0, float(holding_time.get("TP1", 1)))
        hours_to_tp2 = max(2.0, float(holding_time.get("TP2", 2)))
        hours_to_tp3 = max(hours_to_tp2, float(holding_time.get("MAX", hours_to_tp2)))
        max_hold_hours = max(hours_to_tp2, float(holding_time.get("MAX", hours_to_tp2)))

        tp1_deadline_utc = current_time + timedelta(hours=hours_to_tp1)
        tp2_deadline_utc = current_time + timedelta(hours=hours_to_tp2)
        max_hold_deadline_utc = current_time + timedelta(hours=max_hold_hours)

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
def telegram_mode_tag(trade_mode):
    """🧭 MODE tag for Telegram signal messages"""
    return "🧭 MODE: CASHFLOW" if trade_mode == "CASHFLOW" else "🧭 MODE: SWING"

def telegram_trailing_tag(trade_mode):
    """🔁 TRAILING tag for Telegram signal messages"""
    if trade_mode == "CASHFLOW":
        return "🔁 TRAILING: CASHFLOW (Aggressive)"
    return "🔁 TRAILING: SWING (Structure-based)"

def zone_confidence_badge(conf):
    """🏷️ Zone confidence badge: 80+ Strong, 60-79 Medium, 40-59 Weak, 0-39 None"""
    if conf is None:
        return "🏷️ ZONE CONFIDENCE: N/A ⚪ None"
    conf = int(conf)
    if conf >= 80:
        return f"🏷️ ZONE CONFIDENCE: {conf}% 🟢 Strong"
    elif conf >= 60:
        return f"🏷️ ZONE CONFIDENCE: {conf}% 🟡 Medium"
    elif conf >= 40:
        return f"🏷️ ZONE CONFIDENCE: {conf}% 🟠 Weak"
    else:
        return f"🏷️ ZONE CONFIDENCE: {conf}% ⚪ None"

def zone_confidence_bar(conf):
    """🏷️ Zone confidence visual bar (10 segments)"""
    if conf is None:
        return "🏷️ ZONE: N/A ░░░░░░░░░░"
    conf = int(conf)
    filled = min(10, max(0, int(conf / 10)))
    return f"🏷️ ZONE: {conf}% " + "▓" * filled + "░" * (10 - filled)

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
        
        leverage_display = signal.get('leverage', LEVERAGE)
        position_size_usd = signal.get('position_size_usd')
        margin_usd = signal.get('margin_usd')
        size_block = ""
        if position_size_usd:
            size_block = f"<b>Position:</b> ${position_size_usd:.2f}\n"
            if margin_usd:
                size_block += f"<b>Margin:</b> ${margin_usd:.2f} | <b>Lev:</b> {leverage_display}x\n"
        
        current_target = auto_adjust_targets.current_target if AUTO_ADJUST_TARGETS_ENABLED else DAILY_PROFIT_TARGET_EUR
        trade_mode = signal.get("mode", TRADE_MODE)
        mode_label = "CASHFLOW" if trade_mode == "CASHFLOW" else "SWING"
        zone_label = signal.get("zone_context_type") or ""
        trend_label = signal.get("trend") or ""
        rejection_hint = "Rejection" if any("REJECTION" in s for s in signal.get("signals", [])) else ""
        context_parts = [p for p in [zone_label, rejection_hint, f"HTF {trend_label}" if trend_label else ""] if p]
        context_line = " + ".join(context_parts) if context_parts else "Context: N/A"
        if context_parts:
            context_line = f"Context: {context_line}"

        mode_tag = telegram_mode_tag(trade_mode)
        trailing_tag = telegram_trailing_tag(trade_mode)
        zone_conf = signal.get("zone_confidence")
        zone_line = zone_confidence_badge(zone_conf) if zone_conf is not None else ""

        msg_lines = [
            f"{'🟢' if mode_label == 'CASHFLOW' else '🔵'} {mode_label} | {asset_name} (Perp) | {signal['direction']}",
            f"Score: {signal['score']}/100 | Conf: {confidence*100:.0f}%",
            "",
            mode_tag,
            trailing_tag,
        ]
        if zone_line:
            msg_lines.extend(["", zone_line])
        msg_lines.extend([
            "",
            f"Entry: {signal['price']:.2f}",
            f"SL: {signal['sl']:.2f}",
            f"TP1: {signal['tp1']:.2f}  TP2: {signal['tp2']:.2f}  TP3: {signal['tp3']:.2f}",
            "",
            context_line,
        ])
        message = "\n".join(msg_lines)
        
        sent = await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        telegram_stats["last_send"] = datetime.now(timezone.utc).isoformat()
        telegram_stats["last_method"] = "signal"
        telegram_stats["last_message_id"] = getattr(sent, "message_id", None)
        telegram_stats["last_error"] = None
        telegram_stats["last_error_type"] = None
        telegram_stats["last_chat_id_masked"] = mask_chat_id(CHAT_ID)
        record_signal_sent()
        print(f"Signal sent: {asset_name} {signal['direction']}")
    except Exception as e:
        print(f"Telegram error: {e}")
        telegram_stats["last_send"] = datetime.now(timezone.utc).isoformat()
        telegram_stats["last_method"] = "signal"
        telegram_stats["last_message_id"] = None
        telegram_stats["last_error"] = str(e)
        telegram_stats["last_error_type"] = type(e).__name__
        telegram_stats["last_chat_id_masked"] = mask_chat_id(CHAT_ID)

async def send_telegram_auto_trade(signal, trade_result, action="OPEN"):
    """Send Telegram notification for auto-executed trades"""
    if not AUTO_TRADING_ENABLED:
        return
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
            trade_mode = signal.get("mode", TRADE_MODE)
            mode_tag = telegram_mode_tag(trade_mode)
            trailing_tag = telegram_trailing_tag(trade_mode)
            zone_conf = signal.get("zone_confidence")
            zone_line = f"\n{zone_confidence_badge(zone_conf)}" if zone_conf is not None else ""
            message = f"""
🤖 <b>AUTO-TRADE EXECUTED</b> 🤖

{direction_emoji} <b>{asset_name} {signal['direction']}</b>
{tier_emoji} <b>Leverage:</b> {leverage}x ({leverage_tier})
<b>Size:</b> ${trade_result.get('size', 0) * trade_result['price']:.2f}

{mode_tag}
{trailing_tag}{zone_line}

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

async def send_telegram_status(message: str):
    """Send non-critical status message to Telegram."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
    except Exception as e:
        print(f"  ⚠️ Telegram status error: {e}")

async def send_test_signal(
    symbol="PF_XBTUSD",
    direction="LONG",
    price=42000.0,
    sl=41400.0,
    tp1=43000.0,
    tp2=44000.0,
    tp3=45500.0,
    position_size_usd=250.0,
    margin_usd=50.0,
    leverage=5
):
    """Send a test signal to Telegram."""
    signal = {
        "symbol": symbol,
        "direction": direction,
        "score": 88,
        "base_score": 82,
        "signals": ["TEST_SIGNAL", "STRUCTURE_BULL", "EMA_STACK", "VWAP_SUPPORT"],
        "price": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "atr": 250.0,
        "rsi": 48.0,
        "trend": "BULL",
        "confidence": 0.78,
        "modules_used": 6,
        "entry_type": "LIMIT",
        "entry_reason": "🧪 TEST SIGNAL",
        "confirmation_count": 4,
        "time": datetime.now(timezone.utc),
        "position_size_usd": position_size_usd,
        "margin_usd": margin_usd,
        "leverage": leverage,
    }
    await send_telegram_signal(signal)

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
                sl_change_pct = abs((updates['trailing_update'] - old_sl) / old_sl * 100) if old_sl else 999.0
                last_trailing_notify = position.get('last_trailing_notify_time')
                time_ok = last_trailing_notify is None or (datetime.now(timezone.utc) - last_trailing_notify).total_seconds() >= 60
                remaining_size = position.get('remaining_size', position.get('size', 0))
                is_first_place = not position.get('exchange_sl_order_id')
                # Update Kraken: first time (Kraken-imported pos) OR significant move (≥1%)
                should_update_exchange = (
                    TRAILING_STOP_ON_EXCHANGE and POSITION_TRACKING_ENABLED and remaining_size > 0 and
                    (is_first_place or (sl_change_pct >= 1.0 and time_ok))
                )
                if should_update_exchange:
                    await update_exchange_sl(symbol, updates['trailing_update'], remaining_size)
                if sl_change_pct >= 1.0 and time_ok:
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
    global last_signals, signals_history, bot_stats, bear_last_update, htf_last_update, htf_last_candle_time, htf_state
    
    print(f"\n{'='*50}")
    print(f"Checking signals at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print(f"{'='*50}")

    allowed, reason = sunday_engine.is_sunday_trading_time()
    if not allowed:
        print(f"  ⏸️ {reason}")
        return
    
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

    # Bear market update (periodic)
    if bear_engine:
        now = datetime.now(timezone.utc)
        needs_update = (
            bear_last_update is None
            or (now - bear_last_update).total_seconds() > BEAR_MARKET_UPDATE_INTERVAL
        )
        if needs_update:
            try:
                btc_change_7d = await get_btc_7d_change()
                fear_greed = sentiment_analyzer.get_fear_greed_index().get("value", 50)
                market_data = {
                    'btc_change_7d': btc_change_7d,
                    'total_cap_change': btc_change_7d,  # Fallback proxy
                    'fear_greed': fear_greed,
                    'dominant_trend': market_regime_state.get("regime", "NEUTRAL")
                }
                bear_engine.update_market_data(market_data)
                bear_last_update = now
            except Exception as e:
                print(f"  ⚠️ Bear market update error: {e}")

    # HTF structure update (new 1H candle)
    try:
        now = datetime.now(timezone.utc)
        if htf_last_update is None or (now - htf_last_update).total_seconds() >= 300:
            htf_df = await fetch_ohlcv("PF_XBTUSD", TIMEFRAME_TREND, 120)
            if htf_df is not None and len(htf_df) >= 10:
                candle_time = None
                if "timestamp" in htf_df.columns:
                    candle_time = htf_df["timestamp"].iloc[-1]
                else:
                    candle_time = int(now.timestamp())
                if htf_last_candle_time != candle_time:
                    atr_val = calc_atr(htf_df).iloc[-1]
                    candles = htf_df[["open", "high", "low", "close"]].to_dict("records")
                    htf_state = update_htf_structure(candles, atr_val, direction_lock)
                    htf_last_candle_time = candle_time
                    htf_last_update = now
                    hold_long = structure_hold(candles, "LONG")
                    hold_short = structure_hold(candles, "SHORT")
                    print(
                        f"🔒 HTF LOCK: {htf_state.get('lock')} | BOS: {htf_state.get('bos')} | "
                        f"HOLD(L/S): {hold_long}/{hold_short}"
                    )
    except Exception as e:
        print(f"  ⚠️ HTF structure update error: {e}")
    
    # v8.9.19: Collect all valid signals first, then sort by score before auto-trading
    collected_signals = []  # List of (symbol, signal, asset_name) tuples
    
    def normalize_asset_name(sym: str) -> str:
        return ASSET_NAMES.get(sym, sym).replace("PF_", "").replace("USD", "")

    allowed_assets = [normalize_asset_name(s) for s in FUTURES_ASSETS]
    for symbol in FUTURES_ASSETS:
        try:
            asset_name = normalize_asset_name(symbol)
            if asset_name not in allowed_assets:
                continue

            in_supply_zone = False
            in_demand_zone = False
            htf_supply_breakout = False
            htf_demand_breakdown = False
            location_decision = None

            # 4H Macro analysis (big picture)
            df_4h = await fetch_ohlcv(symbol, TIMEFRAME_MACRO, 100)
            macro = analyze_macro(df_4h)
            
            # 1H Trend analysis
            df_1h = await fetch_ohlcv(symbol, TIMEFRAME_TREND, 100)
            trend, trend_score = analyze_trend(df_1h)
            
            # 15m Entry signals
            df_15m = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, 100)

            # LOCATION ENGINE (HTF BOS / LOCK -> zones -> allow/deny)
            try:
                atr_4h = calc_atr(df_4h).iloc[-1] if df_4h is not None else None
                htf_candles = df_4h[["open", "high", "low", "close"]].to_dict("records") if df_4h is not None else []
                ll_printed = lower_low_printed(htf_candles) if htf_candles else False
                bearish_impulse_flag = bearish_impulse(df_4h)
                htf_blocks = OrderBlocks.detect_htf_order_blocks(df_4h, atr_4h, impulse_mult=ZONE_IMPULSE_MULT)
                current_price = df_1h["close"].iloc[-1] if df_1h is not None else 0
                supply_zone = None
                demand_zone = None
                for ob in htf_blocks:
                    zone_type = OrderBlocks.classify_zone(ob)
                    if ob.contains_price(current_price):
                        if zone_type == "SUPPLY":
                            in_supply_zone = True
                            supply_zone = ob
                            supply_zone.atr = atr_4h
                        if zone_type == "DEMAND":
                            in_demand_zone = True
                            demand_zone = ob
                            demand_zone.atr = atr_4h
                last_htf_close = df_4h["close"].iloc[-1] if df_4h is not None else current_price
                htf_supply_breakout = False
                htf_demand_breakdown = False
                near_supply_zone = False
                near_demand_zone = False
                resistance_break = False
                zone_accepted = False
                next_candle_bullish = False
                if supply_zone and atr_4h is not None:
                    htf_supply_breakout = last_htf_close > supply_zone.top + (ZONE_BUFFER_ATR * atr_4h)
                    supply_buffer = 0.25 * atr_4h
                    if (supply_zone.bottom - supply_buffer) <= current_price <= (supply_zone.top + supply_buffer):
                        near_supply_zone = True
                if demand_zone and atr_4h is not None:
                    htf_demand_breakdown = last_htf_close < demand_zone.bottom - (ZONE_BUFFER_ATR * atr_4h)
                    demand_buffer = 0.25 * atr_4h
                    if (demand_zone.bottom - demand_buffer) <= current_price <= (demand_zone.top + demand_buffer):
                        near_demand_zone = True

                supply_rejection_guard = False
                if supply_zone and df_15m is not None and len(df_15m) >= 2:
                    zone_high = supply_zone.top
                    breakout_candle = df_15m.iloc[-2]
                    confirm_candle = df_15m.iloc[-1]
                    resistance_break = breakout_candle["high"] > zone_high
                    breakout_range = breakout_candle["high"] - breakout_candle["low"]
                    breakout_body = abs(breakout_candle["close"] - breakout_candle["open"])
                    breakout_body_pct = breakout_body / breakout_range if breakout_range > 0 else 0
                    zone_accepted = (breakout_candle["close"] > zone_high) and (breakout_body_pct >= 0.6)
                    next_candle_bullish = confirm_candle["close"] >= confirm_candle["open"]
                    confirm_range = confirm_candle["high"] - confirm_candle["low"]
                    confirm_body = abs(confirm_candle["close"] - confirm_candle["open"])
                    confirm_body_pct = confirm_body / confirm_range if confirm_range > 0 else 0
                    touched_supply = breakout_candle["high"] >= zone_high
                    closed_below_supply = breakout_candle["close"] < zone_high
                    next_candle_weak = (confirm_candle["close"] < confirm_candle["open"]) or (confirm_body_pct < 0.3)
                    supply_rejection_guard = touched_supply and closed_below_supply and next_candle_weak

                location_engine = LocationEngine()
                location_decision = location_engine.evaluate(type("LC", (), {
                    "htf_bos": htf_state.get("bos"),
                    "in_supply_zone": in_supply_zone,
                    "in_demand_zone": in_demand_zone,
                    "htf_supply_breakout": htf_supply_breakout,
                    "htf_demand_breakdown": htf_demand_breakdown,
                    "near_supply_zone": near_supply_zone,
                    "near_demand_zone": near_demand_zone,
                    "bearish_impulse": bearish_impulse_flag,
                    "resistance_break": resistance_break,
                    "zone_accepted": zone_accepted,
                    "next_candle_bullish": next_candle_bullish,
                    "supply_rejection_guard": supply_rejection_guard,
                })())
                if not location_decision.allow_long and not location_decision.allow_short:
                    print(f"  → 🚫 LOCATION BLOCK: {location_decision.reason}")
                    record_block("LOCATION_ENGINE")
                    continue
            except Exception as e:
                print(f"  ⚠️ Location engine error: {e}")

            # Zone interaction (computed after signal direction is known)
            zone_interaction = ZoneInteraction.NONE
            zone_context_type = None

            regime_flip_state = "NORMAL"
            if df_1h is not None and len(df_1h) >= 10:
                last_n = df_1h.iloc[-10:]
                green_ratio = (last_n["close"] >= last_n["open"]).mean()
                red_ratio = 1.0 - green_ratio
                if trend in ["BEAR", "STRONG_BEAR"] and green_ratio >= 0.7:
                    regime_flip_state = "POTENTIAL_FLIP"
                if trend in ["BULL", "STRONG_BULL"] and red_ratio >= 0.7:
                    regime_flip_state = "POTENTIAL_FLIP"

            expansion_bias_state = "NORMAL"
            range_atr = 0.0
            atr_1h = calc_atr(df_1h).iloc[-1] if df_1h is not None and len(df_1h) >= 14 else None
            if df_1h is not None and atr_1h:
                if len(df_1h) >= 20:
                    range_atr = (df_1h["high"].iloc[-20:].max() - df_1h["low"].iloc[-20:].min()) / atr_1h
                rsi_1h = calc_rsi(df_1h["close"]).iloc[-1]
                if range_atr >= 3.0 and rsi_1h > 75:
                    expansion_bias_state = "OVEREXTENDED_UP"
                if range_atr >= 3.0 and rsi_1h < 25:
                    expansion_bias_state = "OVEREXTENDED_DOWN"
            
            # v8.9.3: Get quant bias for this asset
            asset_quant_bias = 0
            if QUANT_ENABLED and quant_engine is not None:
                if asset_name in quant_results and quant_results[asset_name]:
                    asset_quant_bias, _ = quant_engine.get_quant_signal_bias(quant_results[asset_name])
            
            df_daily = await fetch_ohlcv(symbol, TIMEFRAME_DAILY, 30)
            df_weekly = await fetch_ohlcv(symbol, TIMEFRAME_WEEKLY, 10)
            signal = generate_entry_signal(
                symbol,
                df_15m,
                trend,
                df_htf=df_1h,
                macro_data=macro,
                df_1h=df_1h,
                df_daily=df_daily,
                df_weekly=df_weekly,
                quant_bias=asset_quant_bias,
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                in_demand_zone=in_demand_zone,
                in_supply_zone=in_supply_zone,
                near_demand_zone=near_demand_zone,
                near_supply_zone=near_supply_zone,
            )
            if signal is None:
                continue

            # ================================
            # Candle Close Confirmation Engine
            # ================================
            direction = signal.get("direction")
            zone_for_signal = None
            if direction == "LONG" and demand_zone and (in_demand_zone or near_demand_zone):
                zone_for_signal = demand_zone
                zone_context_type = "DEMAND"
            elif direction == "SHORT" and supply_zone and (in_supply_zone or near_supply_zone):
                zone_for_signal = supply_zone
                zone_context_type = "SUPPLY"

            zone_resolution_state = signal.get("zone_resolution", "NONE")
            zone_resolution_reason = signal.get("zone_resolution_reason", "")

            if zone_for_signal is not None and df_15m is not None and len(df_15m) >= 2:
                last_closed = df_15m.iloc[-2]

                # Manipulation Candle Detector (wick-dominant)
                atr_15m = calc_atr(df_15m).iloc[-1] if len(df_15m) >= 14 else None
                if atr_15m:
                    candle_range = max(1e-9, last_closed["high"] - last_closed["low"])
                    candle_body = abs(last_closed["close"] - last_closed["open"])
                    wick_ratio = (candle_range - candle_body) / candle_range
                    body_atr = candle_body / atr_15m
                    if wick_ratio > 0.6 and body_atr < 0.8:
                        asset_name = ASSET_NAMES.get(symbol, symbol)
                        print(f"  → 🚫 {asset_name}: Manipulation candle (wick-dominant)")
                        record_block("MANIPULATION_CANDLE")
                        continue

            if zone_resolution_state == "CONFIRMED_REJECTION":
                zone_interaction = ZoneInteraction.TOUCH
            if zone_resolution_state == "CONFIRMED_BREAK":
                zone_interaction = ZoneInteraction.CONFIRMED

            signal["zone_interaction"] = zone_interaction.value
            signal["zone_resolution"] = zone_resolution_state
            signal["zone_resolution_reason"] = zone_resolution_reason
            signal["regime_flip"] = regime_flip_state
            signal["expansion_bias"] = expansion_bias_state

            avg_atr_per_hour = 4.0
            if atr_1h is not None and signal.get("atr"):
                avg_atr_per_hour = atr_1h / signal["atr"] if signal["atr"] else 4.0
            distance_to_target_atr = 0.0
            if signal.get("atr"):
                distance_to_target_atr = abs(signal["tp1"] - signal["price"]) / signal["atr"]
            holding_time = estimate_hold_time(
                HoldTimeContext(
                    distance_to_target_atr=distance_to_target_atr,
                    avg_atr_per_hour=avg_atr_per_hour,
                    zone_interaction=zone_interaction,
                    regime_flip=regime_flip_state,
                )
            )
            regime_hold = signal.get("holding_time")
            if isinstance(regime_hold, dict):
                holding_time = {
                    "TP1": round(regime_hold.get("TP1", holding_time["TP1"]) * 0.6 + holding_time["TP1"] * 0.4, 1),
                    "TP2": round(regime_hold.get("TP2", holding_time["TP2"]) * 0.6 + holding_time["TP2"] * 0.4, 1),
                    "MAX": round(regime_hold.get("MAX", holding_time["MAX"]) * 0.6 + holding_time["MAX"] * 0.4, 1),
                }
            signal["holding_time"] = holding_time

            if not location_decision.allow_long and signal.get("direction") == "LONG":
                print("  → 🚫 LOCATION BLOCK: LONG disabled by HTF/zones")
                record_block("LOCATION_ENGINE")
                continue
            if not location_decision.allow_short and signal.get("direction") == "SHORT":
                print("  → 🚫 LOCATION BLOCK: SHORT disabled by HTF/zones")
                record_block("LOCATION_ENGINE")
                continue

            
            # ================================
            # v8.9.24: 5m ENTRY OPTIMIZER (FUND MODE)
            # ================================
            # 5m NO LONGER generates signals - only optimizes entry for valid 15m signals
            # Fetched later when signal is confirmed, before auto-trading
            df_5m = None  # Will be fetched only if needed for entry optimization
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
                                "strategy_name": "BREAKOUT",  # Box breakout is a breakout strategy
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
                                "strategy_name": "BREAKOUT",  # Box breakout is a breakout strategy
                                "time": datetime.now(timezone.utc)
                            }
            
            # v8.9.24: Box signal overrides regular signal (5m no longer generates signals)
            if box_signal:
                print(f"  📦 BOX BREAKOUT detected: {box_signal['direction']}")
                signal = box_signal
            # ================================

            if signal:
                signal['score'] += TradingHoursOptimizer.trading_hours_penalty(asset_name)
            
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
                if XGBOOST_AVAILABLE and xgb_engine and XGBoostConfig:
                    try:
                        market_data = build_xgb_market_data(df_15m, df_1h, df_4h, signal, current_price)
                        signal = await xgb_engine.enhance_signal(signal, market_data)
                        if "final_score" in signal:
                            signal["score"] = signal["final_score"]
                        if "ml_adjusted_confidence" in signal:
                            signal["confidence"] = signal["ml_adjusted_confidence"]
                        if not XGBoostConfig.SHADOW_MODE and not signal.get("ml_trade_recommendation", True):
                            print(f"  🤖 ML filtered: {symbol} ({signal.get('ml_confidence', 0):.2f})")
                            continue
                    except Exception as e:
                        print(f"  ⚠️ XGBoost enhance error: {e}")

                # Check if position already open on Kraken
                if POSITION_TRACKING_ENABLED and has_open_position(symbol, signal['direction']):
                    print(f"  → Skipped: {signal['direction']} position already open on Kraken")
                    continue

                # Position correlation check (same-direction exposure)
                is_correlated, corr_reason = await check_position_correlation(symbol, signal['direction'], df_reference=df_1h)
                if is_correlated:
                    print(f"  → Correlation blocked: {corr_reason}")
                    continue
                
                # Bear market filters
                if bear_engine:
                    try:
                        bear_candle_body_pct = 0.0
                        bear_atr_pct = 0.0
                        bear_ema_distance = 0.0
                        if df_15m is not None and len(df_15m) >= 14:
                            last_candle = df_15m.iloc[-1]
                            current_price = last_candle['close']
                            candle_body = abs(last_candle['close'] - last_candle['open'])
                            bear_candle_body_pct = (candle_body / current_price * 100) if current_price > 0 else 0
                            atr = calc_atr(df_15m).iloc[-1]
                            bear_atr_pct = (atr / current_price * 100) if current_price > 0 else 0
                            ema9 = calc_ema(df_15m['close'], 9).iloc[-1]
                            bear_ema_distance = (abs(current_price - ema9) / current_price * 100) if current_price > 0 else 0
                        
                        bear_result = bear_engine.process_signal_filters({
                            'asset': asset_name,
                            'direction': signal.get('direction', 'LONG'),
                            'rsi': signal.get('rsi', 50),
                            'candle_body_pct': bear_candle_body_pct,
                            'atr_pct': bear_atr_pct,
                            'ema_distance': bear_ema_distance
                        })
                        
                        if not bear_result.get('bear_market_allowed', True):
                            print(f"  → Bear market blocked: {asset_name} ({bear_result.get('bear_adjustments', {}).get('ema', {}).get('reason', 'FILTER_BLOCK')})")
                            continue
                        
                        if bear_engine.detector.is_bear_market:
                            bear_multiplier = bear_engine.config.RELAXED_PARAMS['position_size_multiplier']
                            if bear_multiplier != 1.0:
                                signal['size_multiplier'] = signal.get('size_multiplier', 1.0) * bear_multiplier
                    except Exception as e:
                        print(f"  ⚠️ Bear market filter error: {e}")

                # Check all filters before sending signal
                is_blocked, block_reasons = check_all_filters(signal['direction'], signal)
                
                if is_blocked:
                    print(f"  → Signal BLOCKED: {', '.join(block_reasons)}")
                    continue
                
                # Entry Timing Filter (FUND MODE) - WAIT → ARM → ENTER
                if df_15m is not None and len(df_15m) >= 14:
                    try:
                        # Calculate required variables for entry timing filter
                        current_rsi = calc_rsi(df_15m['close']).iloc[-1]
                        
                        # Last candle body percentage
                        last_candle = df_15m.iloc[-1]
                        candle_body = abs(last_candle['close'] - last_candle['open'])
                        current_price = last_candle['close']
                        last_candle_body_pct = (candle_body / current_price * 100) if current_price > 0 else 0
                        
                        # Distance from EMA
                        ema9 = calc_ema(df_15m['close'], 9).iloc[-1]
                        distance_from_ema = abs(current_price - ema9)
                        distance_from_ema_pct = (distance_from_ema / current_price * 100) if current_price > 0 else 0
                        
                        # ATR percentage
                        atr = calc_atr(df_15m).iloc[-1]
                        current_atr_pct = (atr / current_price * 100) if current_price > 0 else 0
                        
                        # Impulse candle detection
                        candle_range = last_candle['high'] - last_candle['low']
                        is_impulse_candle = (candle_body > candle_range * 0.6) if candle_range > 0 else False
                        
                        # Get previous state for this symbol
                        previous_state = entry_timing_states.get(symbol, {})
                        previous_distance_pct = previous_state.get('distance_pct', None)
                        
                        # Create timing context and check
                        # FUND TAISYKLĖ: Score NIEKADA negali apeiti entry timing
                        timing_ctx = EntryTimingContext(
                            rsi=current_rsi,
                            candle_body_pct=last_candle_body_pct,
                            distance_from_ema_pct=distance_from_ema_pct,
                            atr_pct=current_atr_pct,
                            impulse_candle=is_impulse_candle,
                            previous_distance_pct=previous_distance_pct,
                            relax_level=get_effective_relax_level("ENTRY_TIMING")
                        )
                        
                        timing_result = entry_timing_filter(timing_ctx)
                        
                        # Update state tracking
                        entry_timing_states[symbol] = {
                            'state': timing_result.state,
                            'distance_pct': distance_from_ema_pct,
                            'last_check': datetime.now(timezone.utc)
                        }
                        
                        # State emoji mapping
                        state_emoji = {
                            'WAIT': '⏳',
                            'ARM': '🎯',
                            'ENTER': '✅'
                        }
                        emoji = state_emoji.get(timing_result.state, '❓')
                        
                        if not timing_result.allowed:
                            override_used = False
                            if (
                                EMA_DISTANCE_OVERRIDE_ENABLED
                                and signal.get("direction") == "SHORT"
                                and trend in ("STRONG_BEAR", "BEAR")
                                and signal.get("score", 0) >= EMA_DISTANCE_OVERRIDE_MIN_SCORE
                                and "EMA" in timing_result.reason
                            ):
                                ema_dist = distance_from_ema_pct
                                if ema_dist <= 0.3:
                                    size_mult = 1.0
                                    override_used = True
                                elif ema_dist <= 0.8:
                                    size_mult = 0.7
                                    override_used = True
                                elif ema_dist <= 1.2:
                                    size_mult = 0.4
                                    override_used = True
                                elif ema_dist <= 1.6:
                                    size_mult = 0.25
                                    override_used = True
                                if override_used:
                                    signal["size_multiplier"] = signal.get("size_multiplier", 1.0) * size_mult
                                    print(f"  → ⚡ EMA override: allow SHORT (dist={ema_dist:.2f}%, size×{size_mult:.2f})")
                            if not override_used:
                                print(f"  → {emoji} {timing_result.state}: {timing_result.state_reason} ({timing_result.reason})")
                                record_block("ENTRY_TIMING")
                                continue
                        else:
                            print(f"  → {emoji} {timing_result.state}: {timing_result.state_reason} - ENTRY ALLOWED")
                    except Exception as e:
                        print(f"  → Entry timing filter error: {e}")
                        # Continue if filter fails (fail-safe)
                
                # Pullback Entry Engine (FUND MODE)
                if df_15m is not None and len(df_15m) >= 50:
                    try:
                        strategy_name = signal.get("strategy_name", "TREND_CONTINUATION")
                        mode = "TREND" if strategy_name == "TREND_CONTINUATION" else "CASHFLOW"

                        # Calculate required variables for pullback entry engine
                        pb_current_price = df_15m['close'].iloc[-1]
                        
                        # EMA 21 (fast) and EMA 50 (slow)
                        ema_21 = calc_ema(df_15m['close'], 21).iloc[-1]
                        ema_50 = calc_ema(df_15m['close'], 50).iloc[-1]
                        
                        # VWAP
                        vwap_series = calc_vwap(df_15m, period=50)
                        vwap_value = vwap_series.iloc[-1] if vwap_series is not None and len(vwap_series) > 0 else pb_current_price
                        
                        # RSI (already calculated above, reuse)
                        pb_rsi = current_rsi
                        
                        # ATR percentage (already calculated above, reuse)
                        pb_atr_pct = current_atr_pct
                        
                        # Candle body percentage (as percentage of range, not price)
                        pb_last_candle = df_15m.iloc[-1]
                        pb_candle_body = abs(pb_last_candle['close'] - pb_last_candle['open'])
                        pb_candle_range = pb_last_candle['high'] - pb_last_candle['low']
                        pb_candle_body_pct = (pb_candle_body / pb_candle_range * 100) if pb_candle_range > 0 else 0

                        # Previous candle body percentage (for impulse fade)
                        if len(df_15m) >= 2:
                            pb_prev_candle = df_15m.iloc[-2]
                            pb_prev_body = abs(pb_prev_candle['close'] - pb_prev_candle['open'])
                            pb_prev_range = pb_prev_candle['high'] - pb_prev_candle['low']
                            pb_prev_candle_body_pct = (pb_prev_body / pb_prev_range * 100) if pb_prev_range > 0 else 0
                        else:
                            pb_prev_candle_body_pct = 0

                        # Volume declining check
                        if 'volume' in df_15m.columns and len(df_15m) >= 2:
                            pb_cur_vol = df_15m['volume'].iloc[-1]
                            pb_prev_vol = df_15m['volume'].iloc[-2]
                            pb_volume_declining = pb_cur_vol < pb_prev_vol
                        else:
                            pb_volume_declining = False
                        
                        # Trend score (from analyze_trend)
                        pb_trend_score = trend_score

                        # EMA break check for late impulse fade
                        pb_ema_mid = (ema_21 + ema_50) / 2
                        pb_ema_not_broken = not (pb_last_candle['low'] <= pb_ema_mid <= pb_last_candle['high'])
                        pb_price_above_ema = pb_current_price >= pb_ema_mid
                        pb_impulse_state = pullback_impulse_states.get(symbol, "NO_IMPULSE")
                        
                        # Create pullback context and evaluate
                        pb_relax = min(2, get_effective_relax_level("PULLBACK_ENGINE"))
                        pullback_ctx = PullbackContext(
                            price=pb_current_price,
                            ema_fast=ema_21,
                            ema_slow=ema_50,
                            vwap=vwap_value,
                            rsi=pb_rsi,
                            atr_pct=pb_atr_pct,
                            candle_body_pct=pb_candle_body_pct,
                            prev_candle_body_pct=pb_prev_candle_body_pct,
                            volume_declining=pb_volume_declining,
                            ema_not_broken=pb_ema_not_broken,
                            trend_score=pb_trend_score,
                            relax_level=pb_relax,
                            impulse_state=pb_impulse_state,
                            price_above_ema=pb_price_above_ema
                        )
                        
                        pb_decision = evaluate_pullback_entry(pullback_ctx)
                        pullback_impulse_states[symbol] = pb_decision.impulse_state
                        pb_relax = min(2, get_effective_relax_level("PULLBACK_ENGINE"))
                        
                        # State emoji mapping
                        pb_state_emoji = {
                            'WAIT': '⏳',
                            'ARM': '🎯',
                            'ENTER': '✅',
                            'BLOCKED': '🚫'
                        }
                        pb_emoji = pb_state_emoji.get(pb_decision.state.value, '❓')
                        
                        print(f"  → 🧭 PULLBACK_STATE: {pb_emoji} {pb_decision.state.value} | {pb_decision.reason} (mode={mode}, relax={pb_relax}, impulse={pb_decision.impulse_state})")
                        
                        # If entry already allowed, pullback cannot veto (only refine)
                        if pb_decision.state != EntryState.ENTER:
                            if pb_decision.state == EntryState.BLOCKED:
                                size_mult = 0.5
                                action = "SIZE_REDUCE"
                            elif pb_decision.state == EntryState.ARM:
                                size_mult = 0.7
                                action = "DELAY"
                            else:
                                size_mult = 0.7
                                action = "DELAY"
                            signal["size_multiplier"] = signal.get("size_multiplier", 1.0) * size_mult
                            signal["entry_type"] = "LIMIT"
                            signal.setdefault("signals", []).append(f"PULLBACK_{action}")
                            print(f"  → 🧭 PULLBACK_ENGINE: {action} (size×{size_mult:.2f})")
                        
                        # Confluence Gate (FUND MODE) - Final check
                        try:
                            # Get confluence score from signal
                            conf_score = signal.get('confluence_score', 0)
                            
                            # Calculate EMA stack (9, 21, 50)
                            conf_ema9 = calc_ema(df_15m['close'], 9).iloc[-1]
                            conf_ema21 = ema_21  # Already calculated above
                            conf_ema50 = ema_50  # Already calculated above
                            conf_current_price = pb_current_price
                            
                            # EMA stack perfect (for LONG: 9 > 21 > 50)
                            if signal['direction'] == "LONG":
                                ema_stack_perfect = conf_ema9 > conf_ema21 > conf_ema50
                                price_above_emas = (conf_current_price > conf_ema9 and 
                                                   conf_current_price > conf_ema21 and 
                                                   conf_current_price > conf_ema50)
                            else:  # SHORT
                                ema_stack_perfect = conf_ema9 < conf_ema21 < conf_ema50
                                price_above_emas = (conf_current_price < conf_ema9 and 
                                                   conf_current_price < conf_ema21 and 
                                                   conf_current_price < conf_ema50)
                            
                            # MACD bullish/bearish
                            from ta.trend import MACD as MACD_Indicator
                            macd_indicator = MACD_Indicator(df_15m['close'])
                            macd_line = macd_indicator.macd().iloc[-1]
                            macd_signal = macd_indicator.macd_signal().iloc[-1]
                            
                            if signal['direction'] == "LONG":
                                macd_bullish = macd_line > macd_signal
                            else:  # SHORT
                                macd_bullish = macd_line < macd_signal  # For SHORT, bearish MACD is "bullish" for the trade
                            
                            # Create confluence context and evaluate
                            conf_ctx = ConfluenceContext(
                                score=conf_score,
                                ema_stack_perfect=ema_stack_perfect,
                                price_above_emas=price_above_emas,
                                macd_bullish=macd_bullish,
                                rsi=pb_rsi,
                                atr_pct=pb_atr_pct,
                                pullback_state=pb_decision.state.value,
                                relax_level=get_effective_relax_level("CONFLUENCE_GATE")
                            )
                            
                            conf_decision = evaluate_confluence_gate(conf_ctx)
                            
                            print(f"  → 🧱 CONFLUENCE_GATE: {conf_decision.value}")
                            
                            # Confluence behavior by mode
                            if TRADE_MODE == "CASHFLOW":
                                if conf_score >= 70:
                                    conf_size = 1.0
                                    conf_tag = "CONF_HIGH"
                                else:
                                    conf_size = 0.5
                                    conf_tag = "CONF_LOW"
                                signal["size_multiplier"] = signal.get("size_multiplier", 1.0) * conf_size
                                signal.setdefault("signals", []).append(conf_tag)
                                print(f"  → 🧱 CONFLUENCE: {conf_tag} → size×{conf_size:.2f}")
                            else:
                                # SWING: confluence can block
                                if conf_decision == ConfluenceDecision.BLOCK:
                                    print(f"  → ❌ FUND MODE: Entry blocked by confluence gate")
                                    record_block("CONFLUENCE_GATE")
                                    continue
                            
                            # Impulse Exhaustion Filter (FUND MODE) - Final entry protection
                            try:
                                # Calculate candle body and range in ATR units
                                impulse_last_candle = df_15m.iloc[-1]
                                impulse_candle_body = abs(impulse_last_candle['close'] - impulse_last_candle['open'])
                                impulse_candle_range = impulse_last_candle['high'] - impulse_last_candle['low']
                                impulse_atr = atr  # Already calculated above
                                
                                candle_body_atr = impulse_candle_body / impulse_atr if impulse_atr > 0 else 0
                                candle_range_atr = impulse_candle_range / impulse_atr if impulse_atr > 0 else 0
                                
                                # Volume spike detection
                                if 'volume' in df_15m.columns and len(df_15m) >= 20:
                                    current_volume = df_15m['volume'].iloc[-1]
                                    avg_volume = df_15m['volume'].rolling(20).mean().iloc[-1]
                                    volume_spike = current_volume > avg_volume * 1.6 if avg_volume > 0 else False
                                else:
                                    volume_spike = False
                                
                                # Distance from EMA (already calculated above)
                                impulse_distance_from_ema_pct = distance_from_ema_pct
                                
                                # Create impulse context and evaluate
                                impulse_ctx = ImpulseContext(
                                    candle_body_atr=candle_body_atr,
                                    candle_range_atr=candle_range_atr,
                                    rsi=pb_rsi,
                                    distance_from_ema_pct=impulse_distance_from_ema_pct,
                                    volume_spike=volume_spike,
                                    pullback_state=pb_decision.state.value,
                                    relax_level=get_effective_relax_level("IMPULSE_FILTER")
                                )
                                
                                impulse_decision = evaluate_impulse_exhaustion(impulse_ctx)
                                impulse_relax = get_effective_relax_level("IMPULSE_FILTER")
                                
                                print(f"  → 🔥 IMPULSE_FILTER: {impulse_decision.value} (relax={impulse_relax})")
                                
                                # FUND MODE: jokio entry jei BLOCK
                                if impulse_decision == ImpulseDecision.BLOCK:
                                    bypass_min = adaptive_impulse_bypass_threshold()
                                    if conf_score >= bypass_min:
                                        print(f"  → ✅ IMPULSE_BYPASS: adaptive confluence {conf_score} (min={bypass_min})")
                                    else:
                                        print(f"  → ❌ FUND MODE: Entry blocked by impulse exhaustion filter (per vėlu)")
                                        record_block("IMPULSE_FILTER")
                                        continue
                            except Exception as e:
                                print(f"  → Impulse exhaustion filter error: {e}")
                                # Continue if filter fails (fail-safe)
                        except Exception as e:
                            print(f"  → Confluence gate error: {e}")
                            # Continue if filter fails (fail-safe)
                    except Exception as e:
                        print(f"  → Pullback entry engine error: {e}")
                        # Continue if filter fails (fail-safe)
                
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
                
                if not profit_mode_allows(signal):
                    print(f"  ⏸️ PROFIT MODE: Signal filtered ({signal.get('score', 0)}/{signal.get('confidence', 0):.2f})")
                    record_block("PROFIT_MODE")
                    continue

                last_sig = last_signals.get(symbol, {})
                last_signal_time = last_sig.get('time')
                last_direction = last_sig.get('direction')
                last_price = last_sig.get('price', 0)
                cooldown = timedelta(minutes=SIGNAL_COOLDOWN_MINUTES)
                
                # Hard cooldown per asset to avoid signal spam
                if last_signal_time and (signal['time'] - last_signal_time) <= cooldown:
                    price_diff = abs(signal['price'] - last_price) / last_price if last_price else 0
                    print(f"  → Signal on cooldown ({SIGNAL_COOLDOWN_MINUTES}m, price diff {price_diff*100:.1f}%)")
                    continue
                else:
                    # HTF direction lock (cashflow/swing engines)
                    try:
                        processed = mode_router.route(TRADE_MODE, signal, direction_lock)
                        if processed is None:
                            print(f"  → HTF LOCK BLOCKED: {signal.get('direction')}")
                            continue
                        signal = processed
                    except Exception as e:
                        print(f"  ⚠️ HTF lock error: {e}")

                    signal["market_state"] = getattr(market_state, "value", market_state)
                    signal["decision_reason"] = decision_reason
                    signal["zone_resolution_state"] = getattr(zone_resolution_state, "value", zone_resolution_state)
                    signal["zone_state"] = zone_state
                    signal["zone_confidence"] = zone_confidence
                    signal["zone_confirmation"] = zone_confirmation
                    signal["zone_source"] = zone_source
                    signal["approach_direction"] = approach_direction
                    signal["indicator_score_value"] = indicator_score_value
                    signal["indicator_bias"] = indicator_bias
                    signal["zone_resolution_reason"] = zone_resolution_reason

                    # v8.9.19: Collect signal for sorted processing instead of immediate execution
                    collected_signals.append({
                        'symbol': symbol,
                        'signal': signal,
                        'asset_name': asset_name,
                        'score': signal.get('score', 0),
                        'confluence_score': signal.get('confluence_score', 0)
                    })
                    print(f"Signal sent: {asset_name} {signal['direction']}")

                # ================================
                # PROFIT MODE: QUICK STRATEGIES
                # ================================
                if PROFIT_MODE_ENABLED:
                    quick_candidates = []
                    if ENABLE_SCALPING:
                        df_5m_quick = await fetch_ohlcv(symbol, TIMEFRAME_5M_OPTIMIZE, 60)
                        scalp_signal = quick_strategies.generate_scalp_signal(df_5m_quick, asset_name)
                        if scalp_signal:
                            quick_candidates.append(scalp_signal)

                    if ENABLE_SWING_TRADING:
                        swing_signal = quick_strategies.generate_swing_signal(df_1h, asset_name)
                        if swing_signal:
                            quick_candidates.append(swing_signal)

                    for quick in quick_candidates:
                        quick_direction = "LONG" if quick['type'] == "LONG" else "SHORT"
                        quick_signal = build_quick_signal(
                            symbol=symbol,
                            direction=quick_direction,
                            base_price=current_price,
                            strategy_name=quick['strategy'],
                            confidence=quick['confidence'],
                            profit_target_pct=quick['profit_target_pct'],
                            stop_loss_pct=quick['stop_loss_pct']
                        )
                        quick_signal['confluence_score'] = signal.get('confluence_score', 0)
                        if not profit_mode_allows(quick_signal):
                            continue
                        if quick_signal.get('confidence', 0) < MIN_SIGNAL_CONFIDENCE:
                            print(f"  → {asset_name} {quick_direction} skipped: low confidence ({quick_signal.get('confidence', 0):.2f})")
                            record_block("MIN_CONFIDENCE")
                            continue

                        quick_last = last_signals.get(symbol, {})
                        quick_last_time = quick_last.get('time')
                        if quick_last_time and (quick_signal['time'] - quick_last_time) <= cooldown:
                            continue

                        collected_signals.append({
                            'symbol': symbol,
                            'signal': quick_signal,
                            'asset_name': asset_name,
                            'score': quick_signal.get('score', 0),
                            'confluence_score': quick_signal.get('confluence_score', 0)
                        })
                        print(f"  💰 Profit mode quick signal: {asset_name} {quick_direction} ({quick['strategy']})")
            
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
            
            last_sig = last_signals.get(symbol, {})
            last_signal_time = last_sig.get('time')
            if isinstance(last_signal_time, str):
                try:
                    last_signal_time = datetime.fromisoformat(last_signal_time)
                except Exception:
                    last_signal_time = None
            cooldown = timedelta(minutes=SIGNAL_COOLDOWN_MINUTES)
            if last_signal_time and (signal['time'] - last_signal_time) <= cooldown:
                print(f"  → Signal on cooldown ({SIGNAL_COOLDOWN_MINUTES}m) for {asset_name}")
                record_block("SIGNAL_COOLDOWN")
                continue

            if signal.get('confidence', 0) < MIN_SIGNAL_CONFIDENCE:
                print(f"  → {asset_name} skipped: low confidence ({signal.get('confidence', 0):.2f})")
                record_block("MIN_CONFIDENCE")
                continue

            # Daily signal limit (max 5 per day)
            global daily_signal_count, daily_signal_reset_date
            now_utc = datetime.now(timezone.utc)
            today_utc = now_utc.date()
            if daily_signal_reset_date != today_utc:
                daily_signal_reset_date = today_utc
                daily_signal_count = 0
            if daily_signal_count >= MAX_SIGNALS_PER_DAY:
                print(f"  → Daily limit reached ({MAX_SIGNALS_PER_DAY} signals today)")
                record_block("DAILY_SIGNAL_LIMIT")
                continue
            daily_signal_count += 1

            # Send Telegram signal
            await send_telegram_signal(signal)
            log_decision(
                symbol,
                signal.get("direction"),
                signal.get("market_state"),
                "SIGNAL_ACCEPTED",
                zone_resolution=signal.get("zone_resolution_state"),
                zone_reason=signal.get("zone_resolution_reason"),
                zone_state=signal.get("zone_state"),
                zone_confidence=signal.get("zone_confidence"),
                zone_confirmation=signal.get("zone_confirmation"),
                zone_source=signal.get("zone_source"),
                approach_direction=signal.get("approach_direction"),
                indicator_score=signal.get("indicator_score_value"),
                indicator_bias=signal.get("indicator_bias"),
            )
            
            last_signals[symbol] = signal
            signals_history.append(signal)
            
            # Track signal for WIN/LOSS marking
            signal_id = add_signal_to_tracking(signal)
            print(f"  → Signal tracked: {signal_id}")
            
            # ================================
            # AUTO-TRADING EXECUTION (v8.6 + v8.9 Dynamic Leverage)
            # ================================
            if AUTO_TRADING_ENABLED:
                original_entry_price = signal.get('price', 0)
                
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
                            
                            # Use optimized limit price when safe
                            optimized_price = entry_opt.optimized_price
                            if (
                                optimized_price
                                and signal.get('entry_type') == "LIMIT"
                                and original_entry_price > 0
                            ):
                                deviation_pct = abs(optimized_price - original_entry_price) / original_entry_price * 100
                                if deviation_pct <= ENTRY_OPTIMIZED_MAX_DEVIATION_PCT:
                                    sl_distance = abs(original_entry_price - signal['sl'])
                                    tp1_distance = abs(signal['tp1'] - original_entry_price)
                                    tp2_distance = abs(signal['tp2'] - original_entry_price)
                                    tp3_distance = abs(signal['tp3'] - original_entry_price)
                                    
                                    signal['price'] = optimized_price
                                    if signal['direction'] == "LONG":
                                        signal['sl'] = optimized_price - sl_distance
                                        signal['tp1'] = optimized_price + tp1_distance
                                        signal['tp2'] = optimized_price + tp2_distance
                                        signal['tp3'] = optimized_price + tp3_distance
                                    else:
                                        signal['sl'] = optimized_price + sl_distance
                                        signal['tp1'] = optimized_price - tp1_distance
                                        signal['tp2'] = optimized_price - tp2_distance
                                        signal['tp3'] = optimized_price - tp3_distance
                                    
                                    print(f"  🎯 5m optimized LIMIT price: ${optimized_price:.2f} (dev {deviation_pct:.2f}%)")
                except Exception as e:
                    print(f"  ⚠️ 5m optimizer error: {e}")
                
                # ================================
                # FUND ENTRY FLOW v1.0 - Centralized entry decision engine
                # futures_signals.py renka duomenis + kviečia entry_flow
                # ================================
                try:
                    # Fetch required dataframes
                    df_15m_fund = await fetch_ohlcv(symbol, TIMEFRAME_ENTRY, 100)
                    df_1h_fund = await fetch_ohlcv(symbol, TIMEFRAME_TREND, 100)
                    df_4h_fund = await fetch_ohlcv(symbol, TIMEFRAME_MACRO, 100)
                    
                    # Get current trend and regime
                    current_trend = signal.get('trend', 'NEUTRAL')
                    current_regime = market_regime_state.get('regime', 'NEUTRAL')
                    current_price = signal.get('price', 0)
                    direction = signal.get('direction', 'LONG')
                    sl = signal.get('sl', 0)
                    tp1 = signal.get('tp1', 0)
                    
                    # Default values (fail-safe)
                    htf_trend = "NEUTRAL"
                    trend_4h = "NEUTRAL"
                    htf_structure_ok = False
                    near_ob = False
                    near_fvg = False
                    near_range_low = False
                    distance_from_vwap_pct = 0.0
                    ema_distance = 0.0
                    rsi_val = 50.0
                    impulse_pct = 0.0
                    impulse_atr_mult = 0.0
                    ltf_structure = False
                    ltf_candle_close_ok = True
                    rr_estimated = 1.5
                    fees_rr_penalty = 0.0
                    
                    # 1. HTF Trend & Structure (1H)
                    if current_trend in ["STRONG_BULL", "BULL"]:
                        htf_trend = current_trend
                    elif current_trend in ["STRONG_BEAR", "BEAR"]:
                        htf_trend = "BEAR"
                    else:
                        htf_trend = "RANGE"
                    
                    # 1a. Determine 4H Trend (for context only - not blocking)
                    if df_4h_fund is not None and len(df_4h_fund) >= 50:
                        try:
                            # Simple trend detection using EMA and price
                            close_4h = df_4h_fund['close']
                            ema21_4h = calc_ema(close_4h, 21)
                            ema50_4h = calc_ema(close_4h, 50)
                            
                            if len(ema21_4h) > 0 and len(ema50_4h) > 0:
                                ema21_val = ema21_4h.iloc[-1]
                                ema50_val = ema50_4h.iloc[-1]
                                price_4h = close_4h.iloc[-1]
                                
                                # Strong trend: EMA alignment + price above/below
                                if ema21_val > ema50_val and price_4h > ema21_val:
                                    trend_4h = "STRONG_BULL"
                                elif ema21_val > ema50_val:
                                    trend_4h = "BULL"
                                elif ema21_val < ema50_val and price_4h < ema21_val:
                                    trend_4h = "STRONG_BEAR"
                                elif ema21_val < ema50_val:
                                    trend_4h = "BEAR"
                                else:
                                    trend_4h = "NEUTRAL"
                        except Exception as e:
                            # Default to NEUTRAL if calculation fails
                            trend_4h = "NEUTRAL"
                    
                    # Check HTF structure (BOS/CHoCH)
                    if df_1h_fund is not None and len(df_1h_fund) >= 50:
                        try:
                            structure_result = analyze_market_structure(df_1h_fund, lookback=50)
                            htf_structure_ok = (
                                structure_result.get('structure_break') is not None or
                                structure_result.get('choch') is not None
                            )
                        except:
                            pass

                    # STRUCTURE HOLD (HTF candles)
                    htf_bias = trend_4h
                    structure_hold_ok = False
                    ll_printed = False
                    strong_bear_impulse = False
                    bearish_impulse_flag = False
                    resistance_break = False
                    zone_accepted = False
                    next_candle_bullish = False
                    supply_rejection_guard = False
                    try:
                        htf_candles = df_4h_fund[["open", "high", "low", "close"]].to_dict("records") if df_4h_fund is not None else []
                        structure_hold_ok = structure_hold(htf_candles, "LONG" if direction == "LONG" else "SHORT")
                        ll_printed = lower_low_printed(htf_candles) if htf_candles else False
                        bearish_impulse_flag = bearish_impulse(df_4h_fund)
                        strong_bear_impulse = bearish_impulse_flag
                        if supply_zone and df_15m_fund is not None and len(df_15m_fund) >= 2:
                            zone_high = supply_zone.top
                            breakout_candle = df_15m_fund.iloc[-2]
                            confirm_candle = df_15m_fund.iloc[-1]
                            resistance_break = breakout_candle["high"] > zone_high
                            breakout_range = breakout_candle["high"] - breakout_candle["low"]
                            breakout_body = abs(breakout_candle["close"] - breakout_candle["open"])
                            breakout_body_pct = breakout_body / breakout_range if breakout_range > 0 else 0
                            zone_accepted = (breakout_candle["close"] > zone_high) and (breakout_body_pct >= 0.6)
                            next_candle_bullish = confirm_candle["close"] >= confirm_candle["open"]
                            confirm_range = confirm_candle["high"] - confirm_candle["low"]
                            confirm_body = abs(confirm_candle["close"] - confirm_candle["open"])
                            confirm_body_pct = confirm_body / confirm_range if confirm_range > 0 else 0
                            touched_supply = breakout_candle["high"] >= zone_high
                            closed_below_supply = breakout_candle["close"] < zone_high
                            next_candle_weak = (confirm_candle["close"] < confirm_candle["open"]) or (confirm_body_pct < 0.3)
                            supply_rejection_guard = touched_supply and closed_below_supply and next_candle_weak
                    except Exception:
                        structure_hold_ok = False
                    signal_quality = "NEUTRAL"
                    if htf_bias == "STRONG_BULL" and direction == "LONG":
                        signal_quality = "HIGH" if structure_hold_ok else "LOW"
                    if htf_bias == "STRONG_BEAR" and direction == "SHORT":
                        signal_quality = "HIGH" if structure_hold_ok else "LOW"
                    
                    # 2. Regime
                    if current_regime not in ["TREND", "BULL", "BEAR", "RANGE", "CHAOTIC"]:
                        current_regime = "TREND"  # Default fail-safe
                    
                    # 3. Location (OB/FVG/Range Low)
                    if df_15m_fund is not None and len(df_15m_fund) >= 20:
                        try:
                            zone_check = OrderBlocks.is_price_at_key_zone(
                                df_15m_fund, direction, tolerance_pct=0.8
                            )
                            near_ob = zone_check.get('zone_type') == 'ORDER_BLOCK'
                            near_fvg = zone_check.get('zone_type') == 'FVG'
                            # Range low: check if price is near recent low
                            if len(df_15m_fund) >= 20:
                                recent_low = df_15m_fund['low'].iloc[-20:].min()
                                near_range_low = abs(current_price - recent_low) / current_price < 0.005  # Within 0.5%
                        except:
                            pass
                    
                    # 4. VWAP Distance
                    if df_1h_fund is not None and len(df_1h_fund) >= 50:
                        try:
                            vwap_series = calc_vwap(df_1h_fund, period=50)
                            if vwap_series is not None and len(vwap_series) > 0:
                                vwap_value = vwap_series.iloc[-1]
                                distance_from_vwap_pct = abs(current_price - vwap_value) / vwap_value * 100
                        except:
                            pass
                    
                    # 4b. EMA distance (15m EMA9)
                    if df_15m_fund is not None and len(df_15m_fund) >= 9 and current_price > 0:
                        try:
                            ema9 = calc_ema(df_15m_fund['close'], 9).iloc[-1]
                            if ema9:
                                ema_distance = abs(current_price - ema9) / current_price * 100
                        except:
                            pass
                    
                    # 5. RSI & Impulse
                    if df_15m_fund is not None and len(df_15m_fund) >= 14:
                        try:
                            rsi_series = calc_rsi(df_15m_fund['close'], period=14)
                            if rsi_series is not None and len(rsi_series) > 0:
                                rsi_val = float(rsi_series.iloc[-1])
                        except:
                            pass
                    
                    # Calculate impulse (price movement over last N candles)
                    if df_15m_fund is not None and len(df_15m_fund) >= 10:
                        try:
                            lookback = 5  # Last 5 candles
                            price_start = df_15m_fund['close'].iloc[-lookback]
                            price_end = df_15m_fund['close'].iloc[-1]
                            impulse_pct = abs(price_end - price_start) / price_start * 100
                            
                            # Impulse in ATR multiples
                            atr_series = calc_atr(df_15m_fund, period=14)
                            if atr_series is not None and len(atr_series) > 0:
                                atr_value = float(atr_series.iloc[-1])
                                impulse_atr_mult = abs(price_end - price_start) / atr_value if atr_value > 0 else 0
                        except:
                            pass
                    
                    # 6. Lower TF Structure (micro BOS/CHoCH on 15m)
                    if df_15m_fund is not None and len(df_15m_fund) >= 30:
                        try:
                            # Simple structure check: price making higher highs (LONG) or lower lows (SHORT)
                            recent_highs = df_15m_fund['high'].iloc[-20:]
                            recent_lows = df_15m_fund['low'].iloc[-20:]
                            
                            if direction == "LONG":
                                # Bullish structure: recent high > previous high
                                if len(recent_highs) >= 2:
                                    ltf_structure = recent_highs.iloc[-1] > recent_highs.iloc[-2]
                            else:  # SHORT
                                # Bearish structure: recent low < previous low
                                if len(recent_lows) >= 2:
                                    ltf_structure = recent_lows.iloc[-1] < recent_lows.iloc[-2]
                        except:
                            pass
                    
                    # 7. Candle close OK (no wick entry)
                    if df_15m_fund is not None and len(df_15m_fund) >= 1:
                        try:
                            last_candle = df_15m_fund.iloc[-1]
                            candle_body = abs(last_candle['close'] - last_candle['open'])
                            candle_range = last_candle['high'] - last_candle['low']
                            # Good close: body is significant portion of range (no large wicks)
                            if candle_range > 0:
                                body_ratio = candle_body / candle_range
                                ltf_candle_close_ok = body_ratio >= 0.5  # At least 50% body
                        except:
                            pass
                    
                    # 8. R:R & Fees
                    if current_price > 0 and sl > 0 and tp1 > 0:
                        if direction == "LONG":
                            sl_distance = abs(current_price - sl) / current_price
                            tp_distance = abs(tp1 - current_price) / current_price
                        else:  # SHORT
                            sl_distance = abs(sl - current_price) / current_price
                            tp_distance = abs(current_price - tp1) / current_price
                        
                        if sl_distance > 0:
                            rr_estimated = tp_distance / sl_distance
                            
                            # Calculate fees penalty (using Kraken fees)
                            # Taker fee: 0.05% entry + 0.05% exit = 0.1% total
                            # Convert to R:R penalty
                            fee_pct = 0.001  # 0.1% total fees
                            fees_rr_penalty = fee_pct / sl_distance if sl_distance > 0 else 0.0
                    
                    # Build MarketContext
                    market_ctx = MarketContext(
                        symbol=symbol,
                        htf_trend=htf_trend,
                        htf_structure_ok=htf_structure_ok,
                        regime=current_regime,
                        trend_4h=trend_4h,
                        direction=direction,
                        near_ob=near_ob,
                        near_fvg=near_fvg,
                        near_range_low=near_range_low,
                        distance_from_vwap_pct=distance_from_vwap_pct,
                        ema_distance=ema_distance,
                        rsi=rsi_val,
                        impulse_pct=impulse_pct,
                        impulse_atr_mult=impulse_atr_mult,
                        ltf_structure=ltf_structure,
                        ltf_candle_close_ok=ltf_candle_close_ok,
                        rr_estimated=rr_estimated,
                        fees_rr_penalty=fees_rr_penalty,
                        htf_bos=htf_state.get("bos"),
                        in_supply_zone=in_supply_zone,
                        in_demand_zone=in_demand_zone,
                        htf_supply_breakout=htf_supply_breakout,
                        htf_demand_breakdown=htf_demand_breakdown,
                        near_supply_zone=near_supply_zone,
                        near_demand_zone=near_demand_zone,
                        htf_bias=htf_bias,
                        structure_hold=structure_hold_ok,
                        signal_quality=signal_quality,
                        strong_bear_impulse=strong_bear_impulse,
                        ll_printed=ll_printed,
                        bearish_impulse=bearish_impulse_flag,
                        resistance_break=resistance_break,
                        zone_accepted=zone_accepted,
                        next_candle_bullish=next_candle_bullish,
                        supply_rejection_guard=supply_rejection_guard,
                        zone_interaction=zone_interaction.value,
                        regime_flip=regime_flip_state,
                        expansion_bias=expansion_bias_state,
                        range_atr=range_atr
                    )
                    
                    # Evaluate entry (async) - entry_flow.py sprendžia
                    decision, reason, ema_multiplier = await evaluate_entry(market_ctx)
                    
                    if decision in (SignalDecision.WAIT, SignalDecision.NO_TRADE):
                        print(f"  ❌ ENTRY BLOCKED [{asset_name}] - {reason}")
                        print(f"     FUND FLOW: HTF={market_ctx.htf_trend}, Regime={market_ctx.regime}, "
                              f"4H (context): {market_ctx.trend_4h}, RSI={market_ctx.rsi:.1f}, RR={market_ctx.rr_estimated:.2f}")
                        if decision == SignalDecision.WAIT:
                            now = datetime.now(timezone.utc)
                            zone_label = zone_context_type or "ZONE"
                            wait_state = f"{zone_label}:{zone_interaction.value}"
                            last_wait = zone_wait_status.get(symbol)
                            should_send = (
                                not last_wait
                                or last_wait.get("state") != wait_state
                                or (now - last_wait.get("last_sent", now)).total_seconds() >= ZONE_WAIT_STATUS_COOLDOWN_MIN * 60
                            )
                            if should_send:
                                await send_telegram_status(
                                    "🟡 <b>MARKET STATE: WAIT</b>\n"
                                    f"{asset_name}: Price interacting with {zone_label} zone\n"
                                    "Awaiting rejection or breakout confirmation"
                                )
                                zone_wait_status[symbol] = {"state": wait_state, "last_sent": now}
                        continue
                    else:
                        # Calculate risk modifier based on 4H trend context
                        risk_modifier = calculate_risk_modifier(market_ctx)
                        
                        print(f"  ✅ ENTRY ALLOWED [{asset_name}] - FUND FLOW")
                        print(f"     HTF={market_ctx.htf_trend}, Regime={market_ctx.regime}, "
                              f"4H (context): {market_ctx.trend_4h}, Location={'OB' if market_ctx.near_ob else 'FVG' if market_ctx.near_fvg else 'Range' if market_ctx.near_range_low else 'N/A'}, "
                              f"RSI={market_ctx.rsi:.1f}, RR={market_ctx.rr_estimated:.2f}, Risk Modifier={risk_modifier}, "
                              f"HTF_BIAS={market_ctx.htf_bias}, STRUCT_HOLD={market_ctx.structure_hold}, QUALITY={market_ctx.signal_quality}")
                        
                        # Store risk_modifier for use in open_position
                        signal['risk_modifier'] = risk_modifier
                        
                        if ema_multiplier < 1.0:
                            signal['size_multiplier'] = signal.get('size_multiplier', 1.0) * ema_multiplier
                            print(f"     📉 EMA distance risk reduction: ×{ema_multiplier:.2f}")
                except Exception as e:
                    print(f"  ⚠️ FUND entry flow error: {e}")
                    # Fail-safe: continue if FUND flow fails
                    # In production, you might want to block on error for safety
                
                if PROFIT_MODE_ENABLED:
                    can_trade, reason = profit_tracker.should_trade_more()
                    if not can_trade:
                        print(f"⏸️ {reason}")
                        continue  # Skip this trade

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
                    scalp_rebound_multiplier=signal.get('scalp_rebound_multiplier', 1.0),
                    strategy_name=signal.get('strategy_name', 'TREND_CONTINUATION'),  # Strategy identification
                    size_multiplier=signal.get('size_multiplier', 1.0),  # Strategy health size multiplier
                    risk_modifier=signal.get('risk_modifier', 1.0)  # 4H trend context risk modifier
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
    # Setup FUND entry flow logger
    set_risk_event_logger(log_risk_event)
    
    print("🚀 Futures Signal Bot v8.9.24 PRO Started!")
    print("📊 v8.9.24: 5m Entry Optimizer (FUND MODE) - 5m tik pagerina entry, neblokuoja signalų!")
    print(f"📊 Assets: {', '.join(ASSET_NAMES.values())}")
    print(f"⏱️ Timeframes: {TIMEFRAME_MACRO} (macro) | {TIMEFRAME_TREND} (trend) | {TIMEFRAME_ENTRY} (entry) | {TIMEFRAME_5M_OPTIMIZE} (optimize)")
    print(f"📱 Telegram: {'Configured' if TELEGRAM_TOKEN else 'NOT CONFIGURED'}")
    if KRAKEN_OBSERVER_MODE:
        if POSITION_TRACKING_ENABLED:
            print(f"👁️ KRAKEN OBSERVER: ON (read-only, tracking positions & stats)")
        else:
            print(f"👁️ KRAKEN OBSERVER: ON but no API keys - add KRAKEN_FUTURES_API_KEY/SECRET to .env")
    print(f"🤖 AUTO-TRADING: {'ON - $' + str(AUTO_TRADE_MARGIN_USD) + ' margin/trade, max ' + str(AUTO_TRADE_MAX_POSITIONS) + ' positions' if AUTO_TRADING_ENABLED else 'OFF'}")
    print(f"💰 Daily Loss Limit: -{DAILY_LOSS_LIMIT_PCT}% (${DAILY_LOSS_LIMIT_USD} fallback)")
    print(f"📊 Weekly Loss Limit: -{WEEKLY_LOSS_LIMIT_PCT}%")
    print(f"📉 Maximum Drawdown: -{MAX_DRAWDOWN_PCT}% from peak equity")
    print(f"📈 Trailing Stop: {'ENABLED' if TRAILING_ENABLED else 'DISABLED'}")
    if TRAILING_ENABLED:
        cfg = TRAILING_MODEL.get(TRADE_MODE, TRAILING_MODEL.get("CASHFLOW", {}))
        if cfg.get("activation_at") == "TP2":
            print(f"   🟢 Mode: {TRADE_MODE} | Trailing after TP2 | Distance: {cfg.get('distance_pct', 3.0)}%")
        else:
            print(f"   🟢 Mode: {TRADE_MODE} | Activate @ {cfg.get('activation_pct', 0.9)}% | Distance: {cfg.get('distance_min', 0.8)}-{cfg.get('distance_max', 1.2)}%")
    if TRAILING_ENABLED and TRAILING_STOP_ON_EXCHANGE and POSITION_TRACKING_ENABLED:
        print(f"🛡️ Trailing on Kraken: ON (SL orders placed & updated on exchange)")
    print(f"🤖 Partial TP: {'ON - Auto-close 33% at TP1 & TP2' if PARTIAL_TP_ENABLED else 'OFF'}")
    print(f"🏛️ FOMC Filter: {'ON' if FOMC_BLACKOUT_ENABLED else 'OFF'}")
    print(f"📉 Market Regime: {'ON' if MARKET_REGIME_ENABLED else 'OFF'}")
    print(f"🌐 Macro Filters: SPY={'ON' if SPY_ENABLED else 'OFF'} VIX={'ON' if VIX_ENABLED else 'OFF'} DXY={'ON' if DXY_ENABLED else 'OFF'}")
    print(f"📍 Position Tracking: {'ON - Skips signals for open positions' if POSITION_TRACKING_ENABLED else 'OFF - No API keys'}")
    print(f"🛡️ FUND Entry Flow: ERROR HANDLING ENABLED - Critical errors will block entry")
    
    # v8.9.14+: Multi-Collateral balance check
    if POSITION_TRACKING_ENABLED:
        balance = fetch_multi_collateral_balance()
        print(f"💵 Account Balance: ${balance['total_usd']:.2f} (Cash: ${balance['cash_usd']:.2f} | Flex: ${balance['flex_usd']:.2f})")
    
    # Initial macro checks
    print("\n--- Running initial macro checks ---")
    detect_macro_market_regime()
    get_spy_data()
    run_macro_checks()
    
    # v8.9.3: Run quant analysis at startup for counter-trend signals
    global quant_results, quant_correlation, quant_last_update
    if QUANT_ENABLED:
        print("\n🧮 Running initial quant analysis...")
        try:
            quant_results, quant_correlation = run_quant_analysis_sync()
            if not quant_results:
                cached = _load_quant_cache()
                if cached and cached.get("assets"):
                    quant_results = cached.get("assets", {})
                    quant_correlation = cached.get("correlation")
                    cached_update = cached.get("last_update")
                    if cached_update:
                        try:
                            quant_last_update = datetime.fromisoformat(cached_update)
                        except Exception:
                            quant_last_update = datetime.now()
            else:
                quant_last_update = datetime.now()
                _save_quant_cache(quant_results, quant_correlation, quant_last_update)
            for asset, data in quant_results.items():
                if data:
                    bias, signals = quant_engine.get_quant_signal_bias(data)
                    print(f"  📊 {asset}: Quant Bias = {bias:+d}")
            print("✅ Quant analysis ready!")
        except Exception as e:
            print(f"⚠️ Quant analysis failed: {e}")
    else:
        quant_results = {}
        quant_correlation = None
        quant_last_update = None

    # Market intel (structure analytics)
    global market_intel_results, market_intel_last_update
    try:
        print("\n🧠 Running market intel analysis...")
        market_intel_results = run_market_intel_sync()
        market_intel_last_update = datetime.now(timezone.utc)
        _save_market_intel_cache(market_intel_results, market_intel_last_update)
        for asset, data in market_intel_results.items():
            if data:
                print(f"  🧠 {asset}: {data.get('bias', 'NEUTRAL')} | {data.get('structure', 'RANGE')} | {data.get('trend_4h', 'NEUTRAL')}")
        print("✅ Market intel ready!")
    except Exception as e:
        print(f"⚠️ Market intel failed: {e}")
    
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
    
    # v8.9.25: Initialize peak equity if not set (first run or after state reset)
    current_equity = get_current_equity()
    if auto_trading_state.get("peak_equity", 0.0) == 0.0 and current_equity > 0:
        auto_trading_state["peak_equity"] = current_equity
        auto_trading_state["peak_equity_date"] = datetime.now(timezone.utc).isoformat()
        print(f"📈 Initial peak equity set: ${current_equity:.2f}")
    elif current_equity > 0:
        # Update peak if current is higher (in case equity increased while bot was off)
        update_peak_equity(current_equity)
        peak = auto_trading_state.get("peak_equity", 0.0)
        if peak > 0:
            drawdown_pct = ((peak - current_equity) / peak * 100) if peak > 0 else 0.0
            print(f"📊 Peak equity: ${peak:.2f} | Current: ${current_equity:.2f} | Drawdown: {drawdown_pct:.2f}%")
    
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
    
    CHECK_SIGNALS_TIMEOUT = 180  # seconds
    while True:
        try:
            # v8.6: Reset daily stats at midnight UTC
            reset_daily_stats()
            
            # Periodic heartbeat (2h) to confirm bot is alive
            last_heartbeat = bot_stats.get("last_heartbeat")
            if not last_heartbeat or (datetime.now(timezone.utc) - last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
                await send_heartbeat()
            
            # Run macro checks every hour
            if (datetime.now(timezone.utc) - last_macro_check).total_seconds() >= 3600:
                print("\n--- Hourly macro update ---")
                detect_macro_market_regime()
                get_spy_data()
                run_macro_checks()
                
                print(f"Regime: {market_regime_state['regime']} | SPY: {spy_state['trend']} | VIX: {macro_state['vix_level']}")
                last_macro_check = datetime.now(timezone.utc)

            # Refresh market intel periodically
            if (market_intel_last_update is None or
                (datetime.now(timezone.utc) - market_intel_last_update).total_seconds() >= MARKET_INTEL_REFRESH_INTERVAL):
                try:
                    market_intel_results = run_market_intel_sync()
                    market_intel_last_update = datetime.now(timezone.utc)
                    _save_market_intel_cache(market_intel_results, market_intel_last_update)
                    print("🧠 Market intel refreshed")
                except Exception as e:
                    print(f"⚠️ Market intel refresh failed: {e}")
            
            # Check if auto-trading is paused due to daily loss limit
            if AUTO_TRADING_ENABLED and auto_trading_state['is_paused']:
                print(f"⚠️ AUTO-TRADING PAUSED: {auto_trading_state['pause_reason']}")
            
            # Sync positions with Kraken every 5 minutes
            await sync_positions_with_kraken()
            
            # Check FOMC blackout
            is_blackout, fomc_date = is_fomc_blackout()
            if is_blackout:
                print(f"🏛️ FOMC BLACKOUT ACTIVE - Signals paused until {(fomc_date + timedelta(hours=FOMC_BLACKOUT_HOURS_AFTER)).strftime('%H:%M')} UTC")
            
            try:
                await asyncio.wait_for(check_signals(), timeout=CHECK_SIGNALS_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"⚠️ check_signals timeout after {CHECK_SIGNALS_TIMEOUT}s - skipping cycle")
            
            # Manage open positions (trailing stops)
            if TRAILING_ENABLED and open_positions:
                print(f"\n--- Managing {len(open_positions)} open position(s) ---")
                await manage_open_positions()
            
            # v8.9.25: Update peak equity periodically (after each cycle)
            current_equity = get_current_equity()
            update_peak_equity(current_equity)
            
            # Adaptive filters status (periodic)
            maybe_log_adaptive_status()
            
            # Increment cycle counters
            bot_stats["cycles_completed"] += 1
            bot_stats["cycles_success"] += 1
            bot_stats["consecutive_errors"] = 0  # Reset error counter on success
            
            # Save state periodically
            save_bot_state()
            
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

@app.route('/favicon.ico')
def favicon():
    """Tuščias favicon – vengti 404 mobiliuose naršyklėse"""
    return Response(b'', status=204, mimetype='image/x-icon')

@app.errorhandler(404)
def not_found(e):
    """Nežinomi URL nukreipiami į pagrindinį dashboard"""
    return redirect(url_for('dashboard'))

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
        "start_time": bot_stats.get("start_time").isoformat() if bot_stats.get("start_time") else None,
    })

@app.route('/api/stats/reset', methods=['POST'])
def api_stats_reset():
    """Nunulinti visą statistiką - signalai, wins, losses, daily/weekly P&L. Dashboard rodys nuo šios dienos."""
    global bot_stats, auto_trading_state
    try:
        # 1. Reset signal_results.json
        fresh_signals = {"signals": [], "stats": {"wins": 0, "losses": 0, "total_profit_pct": 0.0, "ct_wins": 0, "ct_losses": 0}}
        save_signal_results(fresh_signals)
        bot_stats["wins"] = 0
        bot_stats["losses"] = 0
        bot_stats["total_profit_pct"] = 0.0
        # 2. Reset auto_trading_state
        for k in ("daily_pnl", "daily_trades", "daily_wins", "daily_losses", "weekly_pnl", "weekly_trades", "weekly_wins", "weekly_losses"):
            auto_trading_state[k] = 0
        auto_trading_state["is_paused"] = False
        auto_trading_state["pause_reason"] = None
        auto_trading_state["pause_type"] = None
        # 3. Ištrinti bot_state.json
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        # 4. Profit tracker reset (jei yra)
        if profit_tracker:
            try:
                profit_tracker.reset_daily()
            except Exception:
                pass
        return jsonify({"success": True, "message": "Statistika nunulinta"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/profit')
def api_profit():
    now = datetime.now(timezone.utc)
    next_reset = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    seconds_to_reset = int((next_reset - now).total_seconds())
    hours = seconds_to_reset // 3600
    minutes = (seconds_to_reset % 3600) // 60

    current_target = auto_adjust_targets.current_target if AUTO_ADJUST_TARGETS_ENABLED else DAILY_PROFIT_TARGET_EUR
    daily_profit = profit_tracker.daily_profit
    daily_trades = profit_tracker.daily_trades
    daily_wins = auto_trading_state.get("daily_wins", 0)
    win_rate = (daily_wins / max(1, daily_trades)) * 100 if daily_trades else 0
    avg_profit = daily_profit / daily_trades if daily_trades else 0.0

    return jsonify({
        "daily_profit": round(daily_profit, 2),
        "daily_target": round(current_target, 2),
        "daily_trades": daily_trades,
        "win_rate": round(win_rate, 1),
        "avg_profit": round(avg_profit, 2),
        "time_until_reset": f"{hours:02d}:{minutes:02d}",
    })

@app.route('/api/assets/top')
def api_top_assets():
    top_assets = asset_performance.get_best_assets()
    return jsonify({
        "assets": [
            {"asset": asset, "win_rate": round(win_rate, 1), "avg_profit": round(avg_profit, 2)}
            for asset, win_rate, avg_profit in top_assets
        ]
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

def _get_dashboard_url(use_request_host=False):
    """Gauti dashboard URL – Replit, arba lokalus IP (Android / kiti įrenginiai WiFi)"""
    domain = os.getenv("REPLIT_DEV_DOMAIN", "")
    if domain:
        return f"https://{domain}"
    # Jei užkrovimo metu naudotas LAN IP – naudoti tą patį (patikimiau)
    if use_request_host:
        try:
            host = request.host
            if host and "127.0.0.1" not in host and "localhost" not in host:
                return f"http://{host}"
        except Exception:
            pass
    # Lokalus režimas: nustatyti LAN IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return f"http://{local_ip}:5000"
    except Exception:
        pass
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        if local_ip and not local_ip.startswith("127."):
            return f"http://{local_ip}:5000"
    except Exception:
        pass
    return "http://127.0.0.1:5000"


@app.route('/qr')
def qr_code_image():
    """Generuoti QR kodą – skenuokite Android, kad atsidarytų dashboard"""
    url = _get_dashboard_url(use_request_host=True)
    
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
    """Puslapis su QR kodu – skenuokite Android, kad atsidarytų dashboard"""
    url = _get_dashboard_url(use_request_host=True)
    bad_url_warning = ""
    if "127.0.0.1" in url or "localhost" in url:
        bad_url_warning = '''
        <div class="warning-box" style="background:#ff446620;border:1px solid #ff4466;border-radius:10px;padding:15px;margin-bottom:20px;color:#ff4466;">
            ⚠️ <b>Telefonas nepasieks per šį adresą!</b><br>
            Atidarykite šį puslapį per <b>kompiuterio LAN IP</b> (pvz. http://192.168.1.x:5000/share). 
            Paleiskite "ipconfig" arba "ip addr" ir naudokite IPv4 adresą.
        </div>'''
    
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
        ''' + bad_url_warning + '''
        <div class="instructions">
            <h3>📱 Kaip naudoti Android</h3>
            <div class="step">
                <span class="step-num">1</span>
                <span class="step-text"><b>Telefonas turi būti tame pačiame WiFi</b> kaip kompiuteris</span>
            </div>
            <div class="step">
                <span class="step-num">2</span>
                <span class="step-text">Nuskaitykite QR kodą telefono kamera (arba įveskite URL viršuje)</span>
            </div>
            <div class="step">
                <span class="step-num">3</span>
                <span class="step-text">Naršyklė atsidarys – matysite dashboard</span>
            </div>
            <div class="step">
                <span class="step-num">4</span>
                <span class="step-text">(Neprivaloma) Android: Chrome meniu → "Add to Home screen" – kaip programėlė</span>
            </div>
            <div class="step">
                <span class="step-num">5</span>
                <span class="step-text">Jei "Not Found" arba nesijungia: Windows Firewall → leiskite Python per portą 5000</span>
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

@app.route('/api/trades/history')
def api_trades_history():
    """Gauti sandorių istoriją (laimėjimai/pralaimėjimai) iš signal_results.json"""
    data = load_signal_results()
    marked = [s for s in data.get("signals", []) if s.get("result") in ("WIN", "LOSS")]
    recent = list(reversed(marked[-50:]))  # Paskutiniai 50
    trades = []
    for s in recent:
        asset = ASSET_NAMES.get(s.get("symbol", ""), s.get("symbol", "?"))
        trades.append({
            "id": s.get("id", ""),
            "symbol": s.get("symbol", ""),
            "asset": asset,
            "direction": s.get("direction", ""),
            "entry": s.get("entry", 0),
            "result": s.get("result", ""),
            "profit_pct": s.get("profit_pct") or 0,
            "time": s.get("time", ""),
            "marked_at": s.get("marked_at", ""),
            "score": s.get("score", 0),
        })
    return jsonify({
        "trades": trades,
        "stats": data.get("stats", {}),
    })

@app.route('/api/positions')
def api_positions():
    """Gauti atviras pozicijas - open_positions + Kraken pozicijos (observer mode)"""
    TICKER_SYMBOLS = {
        "PF_XBTUSD": "BTC/USD:USD", "PF_ETHUSD": "ETH/USD:USD", "PF_SOLUSD": "SOL/USD:USD",
        "PF_XRPUSD": "XRP/USD:USD", "PF_LTCUSD": "LTC/USD:USD", "PF_ADAUSD": "ADA/USD:USD",
        "PF_DOTUSD": "DOT/USD:USD", "PF_LINKUSD": "LINK/USD:USD",
    }
    positions_data = []
    for symbol in FUTURES_ASSETS:
        asset_name = ASSET_NAMES.get(symbol, symbol)
        pos = open_positions.get(symbol)
        kraken_pos = kraken_positions.get("positions", {}).get(symbol)
        
        if pos:
            # Get current price (use highest/lowest for P&L calculation)
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
                "sl": pos.get('current_sl', pos.get('sl', 0)),
                "tp1": pos.get('tp1', 0),
                "tp2": pos.get('tp2', 0),
                "tp3": pos.get('tp3', 0),
                "trailing_active": pos.get('trailing_active', False),
                "breakeven_active": pos.get('breakeven_active', False),
                "tp1_hit": pos.get('tp1_hit', False),
                "tp2_hit": pos.get('tp2_hit', False),
            })
        elif kraken_pos:
            entry_price = kraken_pos.get("entry_price", 0) or kraken_pos.get("entry", 0)
            direction = kraken_pos.get("direction", "LONG")
            try:
                ticker_sym = TICKER_SYMBOLS.get(symbol)
                ticker = exchange.fetch_ticker(ticker_sym) if ticker_sym else {}
                current_price = ticker.get("last", entry_price) or entry_price
            except Exception:
                current_price = entry_price
            if entry_price > 0:
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if direction == "LONG" else ((entry_price - current_price) / entry_price * 100)
            else:
                pnl_pct = 0
            positions_data.append({
                "asset": asset_name,
                "active": True,
                "direction": direction,
                "entry": entry_price,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "sl": kraken_pos.get("sl", 0),
                "tp1": kraken_pos.get("tp1", 0),
                "tp2": kraken_pos.get("tp2", 0),
                "tp3": kraken_pos.get("tp3", 0),
                "trailing_active": False,
                "breakeven_active": False,
                "tp1_hit": False,
                "tp2_hit": False,
            })
        else:
            positions_data.append({
                "asset": asset_name,
                "active": False,
                "direction": "",
                "entry": 0,
                "current_price": 0,
                "pnl_pct": 0,
                "sl": 0,
                "tp1": 0,
                "tp2": 0,
                "tp3": 0,
                "trailing_active": False,
                "breakeven_active": False,
                "tp1_hit": False,
                "tp2_hit": False,
            })
    
    return jsonify(positions_data)

@app.route('/api/signals')
def api_signals():
    """Visi signalai: live (signals_history) + istorija (signal_results.json)"""
    live = [{
        "symbol": s['symbol'],
        "asset": ASSET_NAMES.get(s['symbol'], s['symbol']),
        "direction": s['direction'],
        "entry": s.get('price', s.get('entry', 0)),
        "price": s.get('price', s.get('entry', 0)),
        "sl": s.get('sl', 0),
        "tp1": s.get('tp1', 0),
        "score": s.get('score', 0),
        "trend": s.get('trend', 'NEUTRAL'),
        "time": s['time'].strftime('%H:%M UTC') if isinstance(s.get('time'), datetime) else str(s.get('time', 'N/A'))[:19],
        "result": None,
        "profit_pct": None,
    } for s in reversed(signals_history[-20:])]
    data = load_signal_results()
    tracked = []
    for s in data.get("signals", [])[-50:]:
        t = s.get("time", "")
        if isinstance(t, str) and len(t) > 10:
            t = t[11:16] + " UTC" if "T" in t else t[:16]
        tracked.append({
            "symbol": s.get("symbol", ""),
            "asset": ASSET_NAMES.get(s.get("symbol", ""), s.get("symbol", "?")),
            "direction": s.get("direction", ""),
            "entry": s.get("entry", 0),
            "price": s.get("entry", 0),
            "sl": s.get("sl", 0),
            "tp1": s.get("tp1", 0),
            "score": s.get("score", 0),
            "trend": "NEUTRAL",
            "time": t or "N/A",
            "result": s.get("result"),
            "profit_pct": s.get("profit_pct"),
        })
    seen_ids = set()
    merged = []
    for item in live + list(reversed(tracked)):
        key = (item.get("asset"), item.get("time"), item.get("entry"))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        merged.append(item)
    merged.sort(key=lambda x: (x.get("time", ""), x.get("asset", "")), reverse=True)
    return jsonify(merged[:50])

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

@app.route('/api/pnl/realtime')
def api_pnl_realtime():
    """
    Real-time P&L tracking endpoint
    Returns current P&L for all open positions and total P&L
    """
    try:
        balance = get_available_balance()
        capital = balance.get("total_usd", 0)
        
        # Calculate P&L for all open positions
        total_unrealized_pnl_usd = 0.0
        total_unrealized_pnl_pct = 0.0
        positions_pnl = []
        
        TICKER_SYMBOLS = {
            "PF_XBTUSD": "BTC/USD:USD",
            "PF_ETHUSD": "ETH/USD:USD",
            "PF_SOLUSD": "SOL/USD:USD",
            "PF_XRPUSD": "XRP/USD:USD",
            "PF_LTCUSD": "LTC/USD:USD",
            "PF_ADAUSD": "ADA/USD:USD",
            "PF_DOTUSD": "DOT/USD:USD",
        }
        
        for symbol, pos in open_positions.items():
            if not pos:
                continue
            
            asset_name = ASSET_NAMES.get(symbol, symbol)
            entry_price = pos.get('entry_price', 0)
            direction = pos.get('direction', 'LONG')
            contracts = pos.get('size', 0)
            size_usd = pos.get('size_usd', 0)
            
            # Get current price
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
            if entry_price > 0 and contracts > 0:
                if direction == "LONG":
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                    pnl_usd = (current_price - entry_price) * contracts
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100
                    pnl_usd = (entry_price - current_price) * contracts
                
                total_unrealized_pnl_usd += pnl_usd
                positions_pnl.append({
                    "symbol": symbol,
                    "asset": asset_name,
                    "direction": direction,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "size_usd": size_usd,
                    "contracts": contracts,
                })
        
        # Calculate total P&L percentage
        if capital > 0:
            total_unrealized_pnl_pct = (total_unrealized_pnl_usd / capital) * 100
        
        # Realized P&L (from closed trades)
        daily_realized_pnl = auto_trading_state.get("daily_pnl", 0)
        weekly_realized_pnl = auto_trading_state.get("weekly_pnl", 0)
        total_realized_pnl = weekly_realized_pnl  # Total realized
        
        # Total P&L (realized + unrealized)
        total_pnl_usd = total_realized_pnl + total_unrealized_pnl_usd
        total_pnl_pct = (total_pnl_usd / capital * 100) if capital > 0 else 0
        
        return jsonify({
            "unrealized": {
                "total_usd": round(total_unrealized_pnl_usd, 2),
                "total_pct": round(total_unrealized_pnl_pct, 2),
                "positions": positions_pnl,
                "count": len(positions_pnl),
            },
            "realized": {
                "daily_usd": round(daily_realized_pnl, 2),
                "weekly_usd": round(weekly_realized_pnl, 2),
                "total_usd": round(total_realized_pnl, 2),
            },
            "total": {
                "pnl_usd": round(total_pnl_usd, 2),
                "pnl_pct": round(total_pnl_pct, 2),
                "capital": round(capital, 2),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"⚠️ P&L realtime endpoint error: {e}")
        return jsonify({
            "error": str(e),
            "unrealized": {"total_usd": 0, "total_pct": 0, "positions": [], "count": 0},
            "realized": {"daily_usd": 0, "weekly_usd": 0, "total_usd": 0},
            "total": {"pnl_usd": 0, "pnl_pct": 0, "capital": 0},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 500

@app.route('/api/strategy/performance')
def api_strategy_performance():
    """
    Strategy performance metrics endpoint
    Returns performance statistics for each strategy
    """
    try:
        data = load_signal_results()
        strategy_stats = {}
        
        # Initialize all strategies
        for strategy in STRATEGY_LIST:
            strategy_stats[strategy] = {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_profit_pct": 0.0,
                "avg_profit_pct": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "recent_trades": [],
                "status": "ACTIVE",  # ACTIVE, WARNING, DISABLED
            }
        
        # Calculate stats for each strategy
        for signal in data.get("signals", []):
            strategy = signal.get("strategy", "TREND_CONTINUATION")
            result = signal.get("result")
            profit_pct = signal.get("profit_pct", 0.0)
            
            if strategy not in strategy_stats:
                continue
            
            if result in ("WIN", "LOSS"):
                stats = strategy_stats[strategy]
                stats["total_trades"] += 1
                
                if result == "WIN":
                    stats["wins"] += 1
                    stats["total_profit_pct"] += profit_pct
                else:
                    stats["losses"] += 1
                    stats["total_profit_pct"] += profit_pct  # Negative for losses
                
                # Add to recent trades (last 20)
                stats["recent_trades"].append({
                    "result": result,
                    "profit_pct": profit_pct,
                    "symbol": signal.get("symbol"),
                    "time": signal.get("marked_at"),
                })
                if len(stats["recent_trades"]) > 20:
                    stats["recent_trades"] = stats["recent_trades"][-20:]
        
        # Calculate metrics for each strategy
        for strategy, stats in strategy_stats.items():
            total = stats["total_trades"]
            if total == 0:
                continue
            
            stats["win_rate"] = round((stats["wins"] / total) * 100, 1)
            stats["avg_profit_pct"] = round(stats["total_profit_pct"] / total, 2)
            
            # Average win/loss
            if stats["wins"] > 0:
                wins = [t["profit_pct"] for t in stats["recent_trades"] if t["result"] == "WIN"]
                if wins:
                    stats["avg_win_pct"] = round(sum(wins) / len(wins), 2)
            
            if stats["losses"] > 0:
                losses = [t["profit_pct"] for t in stats["recent_trades"] if t["result"] == "LOSS"]
                if losses:
                    stats["avg_loss_pct"] = round(sum(losses) / len(losses), 2)
            
            # Expectancy
            if stats["wins"] > 0 and stats["losses"] > 0:
                win_rate_decimal = stats["wins"] / total
                loss_rate_decimal = stats["losses"] / total
                stats["expectancy"] = round(
                    (win_rate_decimal * stats["avg_win_pct"]) + (loss_rate_decimal * stats["avg_loss_pct"]),
                    2
                )
            
            # Profit Factor (gross profit / gross loss)
            if stats["losses"] > 0:
                gross_profit = sum(t["profit_pct"] for t in stats["recent_trades"] if t["result"] == "WIN")
                gross_loss = abs(sum(t["profit_pct"] for t in stats["recent_trades"] if t["result"] == "LOSS"))
                if gross_loss > 0:
                    stats["profit_factor"] = round(gross_profit / gross_loss, 2)
            
            # Strategy health status (from strategy_health_engine if available)
            try:
                health = strategy_health_engine.evaluate_strategy(strategy)
                if health.status == "DISABLED":
                    stats["status"] = "DISABLED"
                    stats["status_reason"] = health.reason
                elif health.status == "WARNING":
                    stats["status"] = "WARNING"
                    stats["status_reason"] = health.reason
            except:
                pass
    
        return jsonify({
            "strategies": strategy_stats,
            "overall": {
                "total_trades": sum(s["total_trades"] for s in strategy_stats.values()),
                "total_wins": sum(s["wins"] for s in strategy_stats.values()),
                "total_losses": sum(s["losses"] for s in strategy_stats.values()),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"⚠️ Strategy performance endpoint error: {e}")
        return jsonify({
            "error": str(e),
            "strategies": {},
            "overall": {"total_trades": 0, "total_wins": 0, "total_losses": 0},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 500

@app.route('/api/risk/exposure')
def api_risk_exposure():
    """
    Risk exposure monitoring endpoint
    Returns current risk metrics and exposure limits
    """
    try:
        balance = get_available_balance()
        capital = balance.get("total_usd", 0)
        
        # Calculate risk limits
        daily_limit = capital * (DAILY_LOSS_LIMIT_PCT / 100) if capital > 0 else DAILY_LOSS_LIMIT_USD
        weekly_limit = capital * (WEEKLY_LOSS_LIMIT_PCT / 100) if capital > 0 else DAILY_LOSS_LIMIT_USD * 2.5
        
        daily_pnl = auto_trading_state.get("daily_pnl", 0)
        weekly_pnl = auto_trading_state.get("weekly_pnl", 0)
        
        # Current exposure (open positions)
        total_exposure_usd = 0.0
        total_risk_usd = 0.0
        positions_risk = []
        
        for symbol, pos in open_positions.items():
            if not pos:
                continue
            
            asset_name = ASSET_NAMES.get(symbol, symbol)
            entry_price = pos.get('entry_price', 0)
            current_sl = pos.get('current_sl', pos.get('sl', 0))
            direction = pos.get('direction', 'LONG')
            size_usd = pos.get('size_usd', 0)
            contracts = pos.get('size', 0)
            
            # Calculate risk (distance to SL)
            if entry_price > 0 and current_sl > 0:
                if direction == "LONG":
                    sl_distance_pct = abs(entry_price - current_sl) / entry_price
                    risk_usd = size_usd * sl_distance_pct
                else:
                    sl_distance_pct = abs(current_sl - entry_price) / entry_price
                    risk_usd = size_usd * sl_distance_pct
            else:
                sl_distance_pct = 0
                risk_usd = 0
            
            total_exposure_usd += size_usd
            total_risk_usd += risk_usd
            
            positions_risk.append({
                "symbol": symbol,
                "asset": asset_name,
                "direction": direction,
                "exposure_usd": round(size_usd, 2),
                "risk_usd": round(risk_usd, 2),
                "sl_distance_pct": round(sl_distance_pct * 100, 2),
                "leverage": pos.get('leverage', 1),
            })
        
        # Risk metrics
        exposure_pct = (total_exposure_usd / capital * 100) if capital > 0 else 0
        risk_pct = (total_risk_usd / capital * 100) if capital > 0 else 0
        
        # Daily/Weekly limit usage
        daily_limit_used_pct = (abs(daily_pnl) / daily_limit * 100) if daily_limit > 0 and daily_pnl < 0 else 0
        weekly_limit_used_pct = (abs(weekly_pnl) / weekly_limit * 100) if weekly_limit > 0 and weekly_pnl < 0 else 0
        
        # Maximum Drawdown (v8.9.25)
        current_equity = get_current_equity()
        peak_equity = auto_trading_state.get("peak_equity", 0.0)
        peak_equity_date = auto_trading_state.get("peak_equity_date")
        
        # Update peak if current equity is higher
        update_peak_equity(current_equity)
        peak_equity = auto_trading_state.get("peak_equity", current_equity)
        
        # Calculate drawdown
        drawdown_usd = peak_equity - current_equity if peak_equity > 0 else 0.0
        drawdown_pct = (drawdown_usd / peak_equity * 100) if peak_equity > 0 else 0.0
        drawdown_limit_used_pct = (drawdown_pct / MAX_DRAWDOWN_PCT * 100) if MAX_DRAWDOWN_PCT > 0 else 0
        
        # Max positions check
        current_positions = len([p for p in open_positions.values() if p])
        max_positions = AUTO_TRADE_MAX_POSITIONS
        
        # Circuit breaker status
        consecutive_losses = circuit_state.get("consecutive_losses", 0)
        is_circuit_breaker_active = consecutive_losses >= MAX_CONSECUTIVE_LOSSES
        
        return jsonify({
        "capital": {
            "total_usd": round(capital, 2),
            "cash_usd": round(balance.get("cash_usd", 0), 2),
            "flex_usd": round(balance.get("flex_usd", 0), 2),
        },
        "exposure": {
            "total_usd": round(total_exposure_usd, 2),
            "total_pct": round(exposure_pct, 2),
            "positions": positions_risk,
            "count": len(positions_risk),
        },
        "risk": {
            "total_risk_usd": round(total_risk_usd, 2),
            "total_risk_pct": round(risk_pct, 2),
            "max_risk_per_trade_usd": MAX_RISK_PER_TRADE_USD,
            "avg_risk_per_position": round(total_risk_usd / len(positions_risk), 2) if positions_risk else 0,
        },
        "limits": {
            "daily": {
                "limit_pct": DAILY_LOSS_LIMIT_PCT,
                "limit_usd": round(daily_limit, 2),
                "current_pnl": round(daily_pnl, 2),
                "used_pct": round(daily_limit_used_pct, 1),
                "remaining_pct": round(100 - daily_limit_used_pct, 1),
            },
            "weekly": {
                "limit_pct": WEEKLY_LOSS_LIMIT_PCT,
                "limit_usd": round(weekly_limit, 2),
                "current_pnl": round(weekly_pnl, 2),
                "used_pct": round(weekly_limit_used_pct, 1),
                "remaining_pct": round(100 - weekly_limit_used_pct, 1),
            },
            "drawdown": {
                "limit_pct": MAX_DRAWDOWN_PCT,
                "peak_equity_usd": round(peak_equity, 2),
                "current_equity_usd": round(current_equity, 2),
                "drawdown_usd": round(drawdown_usd, 2),
                "drawdown_pct": round(drawdown_pct, 2),
                "used_pct": round(drawdown_limit_used_pct, 1),
                "remaining_pct": round(100 - drawdown_limit_used_pct, 1),
                "peak_date": peak_equity_date,
            },
            "positions": {
                "current": current_positions,
                "max": max_positions,
                "remaining": max_positions - current_positions,
            },
        },
        "circuit_breaker": {
            "active": is_circuit_breaker_active,
            "consecutive_losses": consecutive_losses,
            "max_losses": MAX_CONSECUTIVE_LOSSES,
        },
        "trading_status": {
            "is_paused": auto_trading_state.get("is_paused", False),
            "pause_reason": auto_trading_state.get("pause_reason"),
            "pause_type": auto_trading_state.get("pause_type"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"⚠️ Risk exposure endpoint error: {e}")
        return jsonify({
            "error": str(e),
            "capital": {"total_usd": 0, "cash_usd": 0, "flex_usd": 0},
            "exposure": {"total_usd": 0, "total_pct": 0, "positions": [], "count": 0},
            "risk": {"total_risk_usd": 0, "total_risk_pct": 0, "max_risk_per_trade_usd": 0, "avg_risk_per_position": 0},
            "limits": {
                "daily": {"limit_pct": 0, "limit_usd": 0, "current_pnl": 0, "used_pct": 0, "remaining_pct": 100},
                "weekly": {"limit_pct": 0, "limit_usd": 0, "current_pnl": 0, "used_pct": 0, "remaining_pct": 100},
                "drawdown": {"limit_pct": 0, "peak_equity_usd": 0, "current_equity_usd": 0, "drawdown_usd": 0, "drawdown_pct": 0, "used_pct": 0, "remaining_pct": 100, "peak_date": None},
                "positions": {"current": 0, "max": 0, "remaining": 0},
            },
            "circuit_breaker": {"active": False, "consecutive_losses": 0, "max_losses": 0},
            "trading_status": {"is_paused": False, "pause_reason": None, "pause_type": None},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 500

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

@app.route('/api/sunday/status')
def api_sunday_status():
    return jsonify(sunday_engine.get_sunday_status())

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

    if not QUANT_ENABLED:
        return jsonify({
            "enabled": False,
            "message": "Quant analysis disabled"
        })
    
    # v8.9.2: Faster quant refresh (15 minutes instead of 1 hour)
    QUANT_REFRESH_INTERVAL = 900  # 15 minutes
    need_refresh = (quant_last_update is None or 
                    (datetime.now() - quant_last_update).seconds > QUANT_REFRESH_INTERVAL)
    if not quant_results:
        cache = _load_quant_cache()
        if cache and cache.get("assets"):
            quant_results = cache.get("assets", {})
            quant_correlation = cache.get("correlation")
            cached_update = cache.get("last_update")
            if cached_update and not quant_last_update:
                try:
                    quant_last_update = datetime.fromisoformat(cached_update)
                except Exception:
                    quant_last_update = None
    need_refresh = (quant_last_update is None or 
                    (datetime.now() - quant_last_update).seconds > QUANT_REFRESH_INTERVAL or
                    not quant_results)
    
    if need_refresh:
        try:
            print("\n🧮 Paleidžiama matematinė analizė (4 metų duomenys)...")
            new_results, new_correlation = run_quant_analysis_sync()
            if new_results:
                quant_results = new_results
                quant_correlation = new_correlation
                quant_last_update = datetime.now()
                _save_quant_cache(quant_results, quant_correlation, quant_last_update)
            else:
                cached = _load_quant_cache()
                if cached and cached.get("assets"):
                    quant_results = cached.get("assets", {})
                    quant_correlation = cached.get("correlation")
                    cached_update = cached.get("last_update")
                    if cached_update:
                        try:
                            quant_last_update = datetime.fromisoformat(cached_update)
                        except Exception:
                            quant_last_update = datetime.now()
            print("✅ Matematinė analizė baigta!")
        except Exception as e:
            print(f"Quant error: {e}")
            return jsonify({"error": str(e)}), 500
    
    summary = {}
    cached = _load_quant_cache()
    source_assets = quant_results
    source_correlation = quant_correlation
    source_last_update = quant_last_update
    if cached and cached.get("assets"):
        source_assets = cached.get("assets", {})
        source_correlation = cached.get("correlation")
        cached_update = cached.get("last_update")
        if cached_update:
            try:
                source_last_update = datetime.fromisoformat(cached_update)
            except Exception:
                pass
    if not source_assets:
        cache_path = os.path.join(os.path.dirname(__file__), "quant_cache.json")
        return jsonify({
            "error": "Quant cache empty",
            "cache_path": cache_path,
            "last_update": source_last_update.isoformat() if source_last_update else None
        }), 500

    for asset, data in source_assets.items():
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
    if not summary and source_assets:
        print(f"⚠️ Quant API summary empty. Keys: {list(source_assets.keys())}")
    
    return jsonify({
        "assets": summary,
        "correlation": source_correlation,
        "last_update": source_last_update.isoformat() if source_last_update else None,
    })

@app.route('/api/sentiment')
def api_sentiment():
    """Gauti sentimento analizės duomenis"""
    try:
        data = sentiment_analyzer.get_all_sentiments()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/bear_status')
def bear_status():
    summary = bear_engine.get_filter_summary() if bear_engine else {"market_mode": "UNKNOWN"}
    return jsonify(summary)

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

@app.route('/api/market-intel')
def api_market_intel():
    """Market intel snapshot (structure analytics)."""
    global market_intel_results, market_intel_last_update
    if not market_intel_results:
        cached = _load_market_intel_cache()
        if cached and cached.get("assets"):
            market_intel_results = cached.get("assets", {})
            cached_update = cached.get("last_update")
            if cached_update:
                try:
                    market_intel_last_update = datetime.fromisoformat(cached_update)
                    if market_intel_last_update.tzinfo is None:
                        market_intel_last_update = market_intel_last_update.replace(tzinfo=timezone.utc)
                except Exception:
                    market_intel_last_update = None
    return jsonify({
        "assets": market_intel_results,
        "last_update": market_intel_last_update.isoformat() if market_intel_last_update else None
    })

@app.route('/api/telegram-status')
def api_telegram_status():
    """Check last Telegram send status (masked)."""
    return jsonify({
        "configured": bool(TELEGRAM_TOKEN and CHAT_ID),
        "chat_id_masked": telegram_stats.get("last_chat_id_masked") or mask_chat_id(CHAT_ID),
        "last_send": telegram_stats.get("last_send"),
        "last_method": telegram_stats.get("last_method"),
        "last_message_id": telegram_stats.get("last_message_id"),
        "last_error": telegram_stats.get("last_error"),
        "last_error_type": telegram_stats.get("last_error_type"),
    })

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
                
                if (!response.ok && data.error) {
                    document.getElementById('assets').innerHTML = 
                        '<div class="asset-card" style="grid-column: 1/-1;"><p style="color: #ff4466;">Klaida: ' + (data.error || response.status) + '</p></div>';
                    document.getElementById('assets').style.display = 'grid';
                    return;
                }
                if (data.enabled === false) {
                    document.getElementById('assets').innerHTML = 
                        '<div class="asset-card" style="grid-column: 1/-1;"><p style="color: #ffd700; font-size: 1.2em;">Quant analizė išjungta.</p>' +
                        '<p style="color: #888; margin-top: 10px;">Įjunk config.py arba futures_signals.py: QUANT_ENABLED = True</p></div>';
                    document.getElementById('assets').style.display = 'grid';
                    return;
                }
                if (data.error) {
                    document.getElementById('assets').innerHTML = 
                        '<div class="asset-card" style="grid-column: 1/-1;"><p style="color: #ff4466;">Klaida: ' + data.error + '</p>' +
                        '<p style="color: #888; margin-top: 10px;">Bandyk perkrauti puslapį arba paleisk analizę vėliau.</p></div>';
                    document.getElementById('assets').style.display = 'grid';
                    return;
                }
                
                document.getElementById('assets').style.display = 'grid';
                
                if (data.last_update) {
                    document.getElementById('last-update').textContent = 
                        'Atnaujinta: ' + new Date(data.last_update).toLocaleString('lt-LT');
                }
                
                const assetsDiv = document.getElementById('assets');
                assetsDiv.innerHTML = '';
                
                const assetsData = data.assets || {};
                if (Object.keys(assetsData).length === 0) {
                    assetsDiv.innerHTML = '<div class="asset-card" style="grid-column: 1/-1;"><p style="color: #888;">Duomenų nėra. Paleisk botą ir palauk ~30s kol analizė suskaičiuos.</p></div>';
                    return;
                }
                
                for (const [asset, info] of Object.entries(assetsData)) {
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

async def startup_health_check():
    """Run startup health checks before auto-trading."""
    if not AUTO_TRADING_ENABLED:
        return True
    
    issues = []
    
    if not POSITION_TRACKING_ENABLED:
        issues.append("POSITION_TRACKING_DISABLED")
    
    balance = fetch_multi_collateral_balance()
    if balance.get("fetch_failed"):
        issues.append("BALANCE_FETCH_FAILED")
    if balance.get("total_usd", 0) <= 0:
        issues.append("BALANCE_ZERO_OR_UNKNOWN")
    
    kraken_positions['last_fetch'] = None
    await fetch_kraken_positions()
    if kraken_positions.get("fetch_failed") and kraken_positions.get("consecutive_failures", 0) >= 2:
        issues.append("POSITION_FETCH_FAILED")
    
    if issues:
        auto_trading_state['is_paused'] = True
        auto_trading_state['pause_type'] = "EMERGENCY"
        auto_trading_state['pause_reason'] = "STARTUP_HEALTH_CHECK_FAILED"
        log_risk_event("STARTUP_HEALTH_FAIL", f"Issues: {', '.join(issues)}", "critical")
        await send_telegram_critical_alert("STARTUP_HEALTH_FAIL", f"Issues: {', '.join(issues)}")
        return False
    
    eur_usd_rate = get_eur_usd_rate()
    margin_usd_cap = min(AUTO_TRADE_MARGIN_USD, MAX_MARGIN_USD, MAX_MARGIN_EUR * eur_usd_rate)
    daily_limit_pct = DAILY_LOSS_LIMIT_PCT
    
    await send_telegram_status(
        "✅ <b>AUTO-TRADING ACTIVE</b>\n\n"
        f"Margin cap: ${margin_usd_cap:.2f}\n"
        f"Max leverage: {MAX_LEVERAGE}x\n"
        f"Daily loss limit: {daily_limit_pct:.2f}%"
    )
    return True

# ================================
# MAIN
# ================================
def main():
    if not acquire_single_instance_lock():
        print("🟠 Another bot instance is already running. Exiting.")
        raise SystemExit("Another bot instance is already running")
    sanitize_proxy_env()
    # Validate API keys at startup (if auto-trading enabled)
    try:
        validate_api_keys()
        print("✅ API keys validated successfully")
    except ValueError as e:
        # Log critical error and exit
        log_risk_event(
            event_type="API_KEY_ERROR",
            details=str(e),
            severity="critical"
        )
        print(f"❌ FATAL: {e}")
        print("🔴 Bot stopped: API keys invalid or missing")
        raise SystemExit("Bot stopped: API keys invalid")
    except Exception as e:
        # Unexpected error during validation
        log_risk_event(
            event_type="API_KEY_VALIDATION_ERROR",
            details=f"Unexpected error: {str(e)}",
            severity="critical"
        )
        print(f"❌ FATAL: API key validation failed: {e}")
        raise SystemExit("Bot stopped: API key validation failed")
    
    # Start Flask web server in background (optional)
    if ENABLE_HTTP_SERVER:
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

    global xgb_engine
    if XGBOOST_AVAILABLE and XGBoostTradingEngine:
        try:
            xgb_engine = XGBoostTradingEngine()
        except Exception as e:
            print(f"⚠️ XGBoost init failed: {e}")
            xgb_engine = None
    else:
        if XGBOOST_IMPORT_ERROR:
            print(f"⚠️ XGBoost module unavailable: {XGBOOST_IMPORT_ERROR}")
    
    # Startup health checks (auto-trading safety)
    if AUTO_TRADING_ENABLED:
        ok = asyncio.run(startup_health_check())
        if not ok:
            print("🔴 Bot stopped: startup health check failed")
            raise SystemExit("Bot stopped: startup health check failed")
    else:
        asyncio.run(send_telegram_status(
            "<b>SIGNAL-ONLY MODE</b>\n\n"
            "Auto-trading is disabled.\n"
            "Bot will send signals only."
        ))
    
    # Start main signal loop
    asyncio.run(signal_loop())

if __name__ == "__main__":
    main()
