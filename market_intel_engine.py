from dataclasses import dataclass
from typing import Dict, Any
import pandas as pd


@dataclass
class MarketIntelResult:
    bias: str
    trend_1h: str
    trend_4h: str
    structure: str
    bos: str
    choch: str
    rsi_1h: float
    atr_pct_1h: float
    support_4h: float
    resistance_4h: float
    liquidity_sweep: str
    score: float


class MarketIntelEngine:
    """
    Lightweight market-structure analytics.
    Provides trend, structure, volatility and liquidity context.
    """

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            (high - low),
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(window=period, min_periods=period).mean()

    def _trend_label(self, df: pd.DataFrame) -> str:
        if df is None or len(df) < 30:
            return "NEUTRAL"
        close = df["close"]
        ema_fast = self._ema(close, 21).iloc[-1]
        ema_slow = self._ema(close, 55).iloc[-1]
        price = close.iloc[-1]
        if ema_fast > ema_slow and price > ema_fast:
            return "BULL"
        if ema_fast < ema_slow and price < ema_fast:
            return "BEAR"
        return "NEUTRAL"

    def _structure_label(self, df: pd.DataFrame) -> str:
        if df is None or len(df) < 30:
            return "RANGE"
        highs = df["high"].rolling(window=5).max()
        lows = df["low"].rolling(window=5).min()
        swing_highs = highs[(highs == df["high"])].tail(4)
        swing_lows = lows[(lows == df["low"])].tail(4)
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "RANGE"
        hh = swing_highs.iloc[-1] > swing_highs.iloc[-2]
        hl = swing_lows.iloc[-1] > swing_lows.iloc[-2]
        ll = swing_lows.iloc[-1] < swing_lows.iloc[-2]
        lh = swing_highs.iloc[-1] < swing_highs.iloc[-2]
        if hh and hl:
            return "HH_HL"
        if ll and lh:
            return "LL_LH"
        return "RANGE"

    def _liquidity_sweep(self, df: pd.DataFrame) -> str:
        if df is None or len(df) < 5:
            return "NONE"
        last = df.iloc[-1]
        body = abs(last["close"] - last["open"])
        upper_wick = last["high"] - max(last["close"], last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]
        if body == 0:
            body = (last["high"] - last["low"]) * 0.2
        if lower_wick > body * 2 and last["close"] > last["open"]:
            return "DOWN"
        if upper_wick > body * 2 and last["close"] < last["open"]:
            return "UP"
        return "NONE"

    def _swing_levels(self, df: pd.DataFrame) -> tuple:
        if df is None or len(df) < 30:
            return None, None
        swing_high = df["high"].rolling(window=5).max()
        swing_low = df["low"].rolling(window=5).min()
        last_swing_high = swing_high[(swing_high == df["high"])].tail(1)
        last_swing_low = swing_low[(swing_low == df["low"])].tail(1)
        high_val = float(last_swing_high.iloc[-1]) if not last_swing_high.empty else None
        low_val = float(last_swing_low.iloc[-1]) if not last_swing_low.empty else None
        return high_val, low_val

    def _bos_choch(self, df: pd.DataFrame) -> tuple:
        """
        Simple BOS/CHOCH detection using last swing high/low and close.
        """
        if df is None or len(df) < 30:
            return "NONE", "NONE"
        last_close = float(df["close"].iloc[-1])
        last_high, last_low = self._swing_levels(df)
        bos = "NONE"
        choch = "NONE"
        if last_high and last_close > last_high:
            bos = "BOS_UP"
        elif last_low and last_close < last_low:
            bos = "BOS_DOWN"
        structure = self._structure_label(df)
        if structure == "LL_LH" and last_high and last_close > last_high:
            choch = "CHOCH_UP"
        elif structure == "HH_HL" and last_low and last_close < last_low:
            choch = "CHOCH_DOWN"
        return bos, choch

    def analyze_asset(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> Dict[str, Any]:
        trend_1h = self._trend_label(df_1h)
        trend_4h = self._trend_label(df_4h)
        structure = self._structure_label(df_4h)
        liquidity = self._liquidity_sweep(df_1h)
        bos, choch = self._bos_choch(df_4h)

        rsi_1h = float(self._rsi(df_1h["close"]).iloc[-1]) if df_1h is not None else 50.0
        atr_1h = self._atr(df_1h).iloc[-1] if df_1h is not None else 0.0
        price = float(df_1h["close"].iloc[-1]) if df_1h is not None else 0.0
        atr_pct = float((atr_1h / price) * 100) if price > 0 else 0.0

        support_4h = float(df_4h["low"].tail(50).min()) if df_4h is not None else 0.0
        resistance_4h = float(df_4h["high"].tail(50).max()) if df_4h is not None else 0.0

        bias = "NEUTRAL"
        score = 0.0
        if trend_4h == "BEAR" and structure == "LL_LH":
            bias = "SHORT"
            score += 3
        elif trend_4h == "BULL" and structure == "HH_HL":
            bias = "LONG"
            score += 3
        if trend_1h == "BEAR":
            score -= 1
        if trend_1h == "BULL":
            score += 1
        if liquidity in ("UP", "DOWN"):
            score += 0.5

        return MarketIntelResult(
            bias=bias,
            trend_1h=trend_1h,
            trend_4h=trend_4h,
            structure=structure,
            bos=bos,
            choch=choch,
            rsi_1h=rsi_1h,
            atr_pct_1h=atr_pct,
            support_4h=support_4h,
            resistance_4h=resistance_4h,
            liquidity_sweep=liquidity,
            score=score,
        ).__dict__
