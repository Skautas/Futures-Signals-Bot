from enum import Enum
from dataclasses import dataclass
from typing import List


# =========================
# ENUMS
# =========================

class ZoneType(str, Enum):
    SUPPLY = "SUPPLY"
    DEMAND = "DEMAND"


class ZoneResolutionState(str, Enum):
    OUTSIDE = "OUTSIDE"
    ENTERING = "ENTERING"
    INSIDE = "INSIDE"
    WAIT_RESOLUTION = "WAIT_RESOLUTION"
    CONFIRMED_BREAK = "CONFIRMED_BREAK"
    CONFIRMED_REJECTION = "CONFIRMED_REJECTION"


# =========================
# DATA STRUCTURES
# =========================

@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body_pct(self) -> float:
        if self.range == 0:
            return 0.0
        return self.body / self.range


@dataclass
class Zone:
    low: float
    high: float
    zone_type: ZoneType


@dataclass
class ZoneResolutionResult:
    state: ZoneResolutionState
    reason: str


# =========================
# CORE ENGINE
# =========================

class ZoneResolutionEngine:
    """
    Resolves Demand / Supply zones using CLOSE-BASED confirmation.
    Prevents wick-based fake breakouts.
    """

    def __init__(
        self,
        min_body_pct: float = 0.6,
        required_closes: int = 2
    ):
        self.min_body_pct = min_body_pct
        self.required_closes = required_closes

    # -------- helpers --------

    def _close_above_zone(self, candle: Candle, zone: Zone) -> bool:
        return candle.close > zone.high

    def _close_below_zone(self, candle: Candle, zone: Zone) -> bool:
        return candle.close < zone.low

    def _strong_body(self, candle: Candle) -> bool:
        return candle.body_pct >= self.min_body_pct

    def _wick_rejection(self, candle: Candle, zone: Zone) -> bool:
        # Long wick, small body, close not breaking zone
        if candle.body_pct >= self.min_body_pct:
            return False

        if zone.zone_type == ZoneType.SUPPLY:
            return candle.high > zone.high and candle.close <= zone.high

        if zone.zone_type == ZoneType.DEMAND:
            return candle.low < zone.low and candle.close >= zone.low

        return False

    # -------- main logic --------

    def resolve(
        self,
        candles: List[Candle],
        zone: Zone
    ) -> ZoneResolutionResult:
        """
        candles: last N closed candles (at least 2)
        """

        if len(candles) < self.required_closes:
            return ZoneResolutionResult(
                ZoneResolutionState.WAIT_RESOLUTION,
                "Not enough closed candles"
            )

        last = candles[-1]

        # ---- 1. Outside zone ----
        if last.close > zone.high or last.close < zone.low:
            pass
        else:
            return ZoneResolutionResult(
                ZoneResolutionState.INSIDE,
                "Price inside zone — decision blocked"
            )

        # ---- 2. Breakout confirmation ----
        last_n = candles[-self.required_closes:]

        if zone.zone_type == ZoneType.SUPPLY:
            if all(
                self._close_above_zone(c, zone) and self._strong_body(c)
                for c in last_n
            ):
                return ZoneResolutionResult(
                    ZoneResolutionState.CONFIRMED_BREAK,
                    "Confirmed SUPPLY breakout (2 candle close)"
                )

        if zone.zone_type == ZoneType.DEMAND:
            if all(
                self._close_below_zone(c, zone) and self._strong_body(c)
                for c in last_n
            ):
                return ZoneResolutionResult(
                    ZoneResolutionState.CONFIRMED_BREAK,
                    "Confirmed DEMAND breakdown (2 candle close)"
                )

        # ---- 3. Rejection detection ----
        if self._wick_rejection(last, zone):
            return ZoneResolutionResult(
                ZoneResolutionState.CONFIRMED_REJECTION,
                "Wick rejection — no close acceptance"
            )

        # ---- 4. Still unresolved ----
        return ZoneResolutionResult(
            ZoneResolutionState.WAIT_RESOLUTION,
            "Zone touched but not resolved"
        )
