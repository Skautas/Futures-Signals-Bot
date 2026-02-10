"""
FUND ENTRY FLOW – v1.0 (async-ready)
Centralized entry decision engine for FUND trading mode.
Multi-stage filtering system to ensure high-quality entries only.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
from datetime import datetime, timezone
from engine.location_engine import LocationEngine

try:
    from config import PROFIT_MODE_ENABLED
except Exception:
    PROFIT_MODE_ENABLED = False


# Risk event logger callback (injected from futures_signals.py)
_risk_event_logger: Optional[Callable[[str, str, str], None]] = None


def set_risk_event_logger(logger_func: Callable[[str, str, str], None]):
    """Set the risk event logger function from futures_signals.py"""
    global _risk_event_logger
    _risk_event_logger = logger_func


def _log_risk_event(event_type: str, message: str, severity: str = "warning"):
    """Internal risk event logger with fallback"""
    global _risk_event_logger
    if _risk_event_logger:
        try:
            _risk_event_logger(event_type, message, severity)
        except Exception:
            # Fallback to print if logger fails
            print(f"⚠️ RISK EVENT: {event_type} - {message} (severity: {severity})")
    else:
        # Fallback if logger not set
        print(f"⚠️ RISK EVENT: {event_type} - {message} (severity: {severity})")


@dataclass
class MarketContext:
    symbol: str

    htf_trend: str
    htf_structure_ok: bool
    regime: str
    trend_4h: str              # 4H trend for context only (not blocking)
    direction: str             # "LONG" | "SHORT"

    near_ob: bool
    near_fvg: bool
    near_range_low: bool
    distance_from_vwap_pct: float
    ema_distance: float

    rsi: float
    impulse_pct: float
    impulse_atr_mult: float

    ltf_structure: bool
    ltf_candle_close_ok: bool

    rr_estimated: float
    fees_rr_penalty: float
    htf_bos: Optional[str] = None
    in_supply_zone: bool = False
    in_demand_zone: bool = False
    htf_supply_breakout: bool = False
    htf_demand_breakdown: bool = False
    near_supply_zone: bool = False
    near_demand_zone: bool = False
    htf_bias: Optional[str] = None
    structure_hold: bool = False
    signal_quality: str = "NEUTRAL"
    strong_bear_impulse: bool = False
    ll_printed: bool = False
    bearish_impulse: bool = False
    resistance_break: bool = False
    zone_accepted: bool = False
    next_candle_bullish: bool = False
    supply_rejection_guard: bool = False
    zone_interaction: str = "NONE"
    regime_flip: str = "NORMAL"
    expansion_bias: str = "NORMAL"
    range_atr: float = 0.0


class SignalDecision(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


def relax_ema_filter_for_profit_mode(ema_distance: float) -> tuple:
    """
    Relax EMA distance filter for profit mode.
    Returns: (allowed: bool, reason: str, position_multiplier: float)
    """
    if not PROFIT_MODE_ENABLED:
        # FUND mode - strict
        if ema_distance > 0.7:
            return False, "TOO_FAR_FROM_EMA", 1.0
        return True, "OK", 1.0
    
    # PROFIT mode - relaxed
    if ema_distance > 1.5:
        return False, "TOO_FAR_FROM_EMA", 1.0
    if ema_distance > 0.8:
        # Allow but reduce position
        return True, "HIGH_EMA_DISTANCE_REDUCED_RISK", 0.7
    
    return True, "OK", 1.0


async def evaluate_entry(ctx: MarketContext):
    """
    Centralized FUND entry decision engine (async-ready)
    Returns: (decision: SignalDecision, reason: str, position_multiplier: float)
    """
    try:
        # =========================
        # FUND ENTRY FLOW LOGIC
        # =========================

        # 1️⃣ HTF FILTER
        # ✅ 4H NAUDOJAMAS TIK KONTEXTUI – NE ENTRY BLOKAVIMUI
        # 4H trendas NEBLOKUOJA entry
        # ❌ SENAS BLOKAVIMAS (BLOGAI CASHFLOW BOTUI) - PAŠALINTAS
        # if ctx.trend_4h in ["BEAR", "NEUTRAL"]:
        #     return EntryDecision(allow=False, reason="BLOCKED_BY_4H_TREND")
        pass

        if not ctx.htf_structure_ok:
            return SignalDecision.NO_TRADE, "HTF_STRUCTURE_BLOCK", 1.0

        # 2️⃣ REGIME
        if ctx.regime in ["RANGE", "CHAOTIC"]:
            return SignalDecision.NO_TRADE, "REGIME_BLOCK", 1.0

        # 2.5️⃣ HTF TREND (new hard gate)
        if ctx.htf_trend in ["BEAR", "STRONG_BEAR"] and ctx.direction == "LONG":
            return SignalDecision.NO_TRADE, "HTF_TREND_BLOCK", 1.0
        if ctx.htf_trend in ["BULL", "STRONG_BULL"] and ctx.direction == "SHORT":
            return SignalDecision.NO_TRADE, "HTF_TREND_BLOCK", 1.0

        # 3️⃣ LOCATION (hard gate)
        location_engine = LocationEngine()
        location_decision = location_engine.evaluate(ctx)
        if ctx.direction == "LONG" and not location_decision.allow_long:
            print(f"[LOCATION BLOCK] LONG blocked: {location_decision.reason}")
            return SignalDecision.NO_TRADE, "LOCATION_BLOCK", 1.0
        if ctx.direction == "SHORT" and not location_decision.allow_short:
            print(f"[LOCATION BLOCK] SHORT blocked: {location_decision.reason}")
            return SignalDecision.NO_TRADE, "LOCATION_BLOCK", 1.0

        # 3.5️⃣ ZONE INTERACTION STATE (hard gate)
        if ctx.zone_interaction in ["TOUCH", "INSIDE", "ARMED"]:
            print("⛔ NO TRADE: price interacting with zone, waiting reaction")
            return SignalDecision.WAIT, "ZONE_INTERACTION_WAIT", 1.0

        # 3.6️⃣ REGIME FLIP FILTER (block counter-trend)
        is_counter_trend = (
            (ctx.htf_trend in ["BEAR", "STRONG_BEAR"] and ctx.direction == "LONG")
            or (ctx.htf_trend in ["BULL", "STRONG_BULL"] and ctx.direction == "SHORT")
        )
        if ctx.regime_flip == "POTENTIAL_FLIP" and is_counter_trend:
            return SignalDecision.NO_TRADE, "POTENTIAL_FLIP_BLOCK", 1.0

        # 3.7️⃣ EXPANSION BIAS NEUTRALIZER
        if ctx.expansion_bias == "OVEREXTENDED_UP" and ctx.direction == "SHORT":
            return SignalDecision.NO_TRADE, "EXPANSION_OVEREXTENDED_UP", 1.0
        if ctx.expansion_bias == "OVEREXTENDED_DOWN" and ctx.direction == "LONG":
            return SignalDecision.NO_TRADE, "EXPANSION_OVEREXTENDED_DOWN", 1.0

        if ctx.distance_from_vwap_pct > 1.2:
            return SignalDecision.NO_TRADE, "TOO_FAR_FROM_VWAP", 1.0
        
        ema_allowed, ema_reason, ema_multiplier = relax_ema_filter_for_profit_mode(ctx.ema_distance)
        if not ema_allowed:
            return SignalDecision.NO_TRADE, ema_reason, 1.0

        # 4️⃣ FOMO / EXTENSION
        if ctx.rsi >= 75:
            return SignalDecision.NO_TRADE, "RSI_OVEREXTENDED", 1.0

        if ctx.impulse_atr_mult >= 2.5:
            return SignalDecision.NO_TRADE, "IMPULSE_TOO_STRONG", 1.0

        if ctx.impulse_pct >= 3.5:
            return SignalDecision.NO_TRADE, "PARABOLIC_MOVE", 1.0

        # 5️⃣ LTF CONFIRMATION
        if not ctx.ltf_structure:
            return SignalDecision.NO_TRADE, "NO_LTF_STRUCTURE", 1.0

        if not ctx.ltf_candle_close_ok:
            return SignalDecision.NO_TRADE, "WICK_ENTRY_BLOCKED", 1.0

        # 6️⃣ R:R AFTER FEES
        net_rr = ctx.rr_estimated - ctx.fees_rr_penalty
        if net_rr < 0.8:
            return SignalDecision.NO_TRADE, "RR_TOO_LOW_AFTER_FEES", 1.0

        decision = SignalDecision.LONG if ctx.direction == "LONG" else SignalDecision.SHORT
        return decision, "ENTRY_APPROVED", ema_multiplier * location_decision.position_multiplier

    except Exception as e:
        # Critical error in FUND entry flow - block entry and log
        error_message = f"FUND entry flow error for {ctx.symbol}: {str(e)}"
        _log_risk_event(
            event_type="FUND_FLOW_ERROR",
            message=error_message,
            severity="critical"
        )
        return SignalDecision.NO_TRADE, "FUND_FLOW_ERROR", 1.0


def calculate_risk_modifier(ctx: MarketContext) -> float:
    """
    Calculate position size risk modifier based on 4H trend context.
    4H trend is used only for position sizing, NOT for entry blocking.
    
    Returns:
        float: Risk modifier (0.7 for conflicting 4H, 1.0 otherwise)
    """
    risk_modifier = 1.0
    
    if ctx.trend_4h == "STRONG_BEAR" and ctx.direction == "LONG":
        risk_modifier = 0.7  # mažesnė pozicija
    
    if ctx.trend_4h == "STRONG_BULL" and ctx.direction == "SHORT":
        risk_modifier = 0.7
    
    return risk_modifier

