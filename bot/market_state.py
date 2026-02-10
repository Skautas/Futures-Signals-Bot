from enum import Enum


class MarketState(Enum):
    STRONG_BEAR = "STRONG_BEAR"
    BEAR = "BEAR"
    RANGE = "RANGE"
    BULL = "BULL"
    STRONG_BULL = "STRONG_BULL"


def detect_market_state(htf_structure, htf_bos):
    if htf_structure == "BEAR" and htf_bos == "DOWN":
        return MarketState.STRONG_BEAR

    if htf_structure == "BULL" and htf_bos == "UP":
        return MarketState.STRONG_BULL

    if htf_structure == "BEAR":
        return MarketState.BEAR

    if htf_structure == "BULL":
        return MarketState.BULL

    return MarketState.RANGE
