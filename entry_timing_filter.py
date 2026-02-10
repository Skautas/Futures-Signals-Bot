from dataclasses import dataclass
from typing import Optional


@dataclass
class EntryTimingContext:
    rsi: float
    candle_body_pct: float     # žvakės kūnas % nuo kainos
    distance_from_ema_pct: float
    atr_pct: float
    impulse_candle: bool
    previous_distance_pct: Optional[float] = None  # Ankstesnė distance nuo EMA (state tracking)
    relax_level: int = 0


@dataclass
class EntryTimingResult:
    allowed: bool
    reason: str
    state: str  # "WAIT", "ARM", "ENTER"
    state_reason: str  # Paaiškinimas kodėl ši būsena

ENTRY_TIMING_ENABLED = False
IMPULSE_TIMING_BLOCK_ENABLED = False


def entry_timing_filter(ctx: EntryTimingContext) -> EntryTimingResult:
    """
    FUND MODE entry discipline su būsenų seka:
    WAIT → ARM → ENTER
    
    WAIT – trend OK, bet per karšta (per toli nuo EMA/VWAP)
    ARM – kaina grįžo link EMA/VWAP
    ENTER – candle close + low volatility
    
    FUND TAISYKLĖ: Score NIEKADA negali apeiti entry timing.
    Bypass paliekam tik TP / management, bet NE entry.
    """

    if not ENTRY_TIMING_ENABLED:
        return EntryTimingResult(
            allowed=True,
            reason="ENTRY_TIMING_DISABLED",
            state="ENTER",
            state_reason="Entry timing disabled"
        )

    # 2️⃣ Impulsinė žvakė - blokuoja tik jei įjungta
    if IMPULSE_TIMING_BLOCK_ENABLED and (ctx.impulse_candle or ctx.candle_body_pct > 0.6):
        return EntryTimingResult(
            allowed=False,
            reason="ENTRY_BLOCKED: IMPULSE_CANDLE",
            state="WAIT",
            state_reason="Impulsinė žvakė"
        )

    # Dynamic thresholds based on volatility (adaptive EMA distance)
    relax = max(0, int(ctx.relax_level or 0))
    base_wait = 1.2
    base_enter = 0.25
    base_low_vol = 1.0
    vol_scale = 1.0
    if ctx.atr_pct > 1.0:
        vol_scale += min(0.5, (ctx.atr_pct - 1.0) / 2.0)
    vol_scale *= (1.0 + min(0.25, 0.05 * relax))
    wait_thr = base_wait * vol_scale
    enter_thr = base_enter * vol_scale
    low_vol_thr = base_low_vol * vol_scale
    vol_block = (1.8 + (0.1 * relax)) if ctx.atr_pct <= 1.6 else (2.2 + (0.1 * relax))

    # 3️⃣ Per didelis momentinis volatility - visada blokuoja
    if ctx.atr_pct > vol_block:
        return EntryTimingResult(
            allowed=False,
            reason=f"ENTRY_BLOCKED: VOLATILITY_SPIKE ({ctx.atr_pct:.2f}%)",
            state="WAIT",
            state_reason="Volatility spike"
        )

    # BŪSENŲ LOGIKA
    # WAIT: Per toli nuo EMA/VWAP (> dynamic %)
    if ctx.distance_from_ema_pct > wait_thr:
        return EntryTimingResult(
            allowed=False,
            reason=f"ENTRY_WAIT: TOO_FAR_FROM_EMA ({ctx.distance_from_ema_pct:.2f}%)",
            state="WAIT",
            state_reason=f"Per toli nuo EMA ({ctx.distance_from_ema_pct:.2f}%)"
        )

    # ENTER: Candle close + low volatility + arti EMA
    # Sąlygos:
    # - Distance nuo EMA <= dynamic (arti EMA)
    # - ATR <= dynamic (low volatility)
    # - Ne impulsinė žvakė (jau patikrinta aukščiau)
    if ctx.distance_from_ema_pct <= enter_thr and ctx.atr_pct <= low_vol_thr:
        return EntryTimingResult(
            allowed=True,
            reason="ENTRY_ENTER: READY",
            state="ENTER",
            state_reason=f"Kaina arti EMA ({ctx.distance_from_ema_pct:.2f}%), low vol ({ctx.atr_pct:.2f}%)"
        )

    # ARM: Kaina grįžo link EMA/VWAP (distance mažėja)
    # Sąlygos:
    # - Distance <= dynamic (arti EMA, bet dar ne ENTER)
    # - ARBA buvo WAIT ir dabar artėja
    if ctx.distance_from_ema_pct <= wait_thr:
        # Patikrinti ar buvo WAIT (transition detection)
        was_waiting = ctx.previous_distance_pct is not None and ctx.previous_distance_pct > wait_thr
        is_approaching = ctx.previous_distance_pct is not None and ctx.previous_distance_pct > ctx.distance_from_ema_pct
        
        if was_waiting or is_approaching:
            return EntryTimingResult(
                allowed=False,
                reason=f"ENTRY_ARM: APPROACHING_EMA ({ctx.distance_from_ema_pct:.2f}%)",
                state="ARM",
                state_reason=f"Kaina grįžta link EMA ({ctx.distance_from_ema_pct:.2f}%)"
            )
        else:
            # Arti EMA bet dar ne ready (volatility arba distance)
            if ctx.atr_pct > low_vol_thr:
                return EntryTimingResult(
                    allowed=False,
                    reason=f"ENTRY_ARM: WAITING_LOW_VOL ({ctx.atr_pct:.2f}%)",
                    state="ARM",
                    state_reason=f"Laukiam low volatility (dabar {ctx.atr_pct:.2f}%)"
                )
            else:
                return EntryTimingResult(
                    allowed=False,
                    reason=f"ENTRY_ARM: WAITING_CLOSER ({ctx.distance_from_ema_pct:.2f}%)",
                    state="ARM",
                    state_reason=f"Laukiam artesnio EMA (dabar {ctx.distance_from_ema_pct:.2f}%, reikia <= {enter_thr:.2f}%)"
                )

    # Default: WAIT
    return EntryTimingResult(
        allowed=False,
        reason=f"ENTRY_WAIT: ({ctx.distance_from_ema_pct:.2f}% nuo EMA)",
        state="WAIT",
        state_reason=f"Laukiam artesnio EMA ({ctx.distance_from_ema_pct:.2f}%)"
    )

