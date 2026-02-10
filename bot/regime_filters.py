from bot.market_regime import MarketRegime


def regime_flip_block(direction: str, regime: MarketRegime) -> bool:
    if regime == MarketRegime.POST_CAPITULATION:
        return direction == "SHORT"

    if regime == MarketRegime.EXPANSION_UP:
        return direction == "SHORT"

    if regime == MarketRegime.EXPANSION_DOWN:
        return direction == "LONG"

    return False
