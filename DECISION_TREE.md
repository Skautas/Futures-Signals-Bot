Decision Tree (Market State) - Pseudocode

Goal
- Validate real signals through a strict decision tree.
- Order of gates: Market State -> Direction Gate -> Location -> Zone Resolution -> Breakout/Rejection -> Entry Delay -> Indicators.

Inputs (per asset)
- HTF structure, HTF BOS
- Location (Supply/Demand/Mid)
- Zone resolution state (from close-based zone engine)
- Pullback, fake breakout, rejection, entry delay results
- HTF S/R context (daily/weekly)
- Regime/expansion context
- Mode (CASHFLOW or SWING)

Pseudocode

function evaluate_signal(ctx):
    # 0) HTF S/R gate (outside decision engine, before tree)
    if htf_sr_blocks(ctx):
        return BLOCK("HTF_SR")

    # 1) Market State
    market_state = detect_market_state(htf_structure, htf_bos)

    # 2) Direction Gate
    if mode == "SWING":
        allow_long = (market_state == STRONG_BULL)
        allow_short = (market_state == STRONG_BEAR)
    else:
        allow_long, allow_short = direction_gate(market_state)

    if direction == LONG and not allow_long:
        return BLOCK("MARKET_STATE_LONG_DISABLED")
    if direction == SHORT and not allow_short:
        return BLOCK("MARKET_STATE_SHORT_DISABLED")

    # 3) Location (Supply/Demand proximity)
    if location == AT_SUPPLY and direction == LONG:
        return BLOCK("LONG_AT_SUPPLY")
    if location == AT_DEMAND and direction == SHORT:
        return BLOCK("SHORT_AT_DEMAND")

    # 4) Zone Resolution (close-based acceptance)
    if zone_state in {INSIDE, WAIT_RESOLUTION}:
        return BLOCK("ZONE_RESOLUTION_WAIT")
    if zone_state == CONFIRMED_BREAK:
        if direction == LONG and location in {AT_DEMAND, BELOW_DEMAND}:
            return BLOCK("DEMAND_BREAKDOWN_CONFIRMED")
        if direction == SHORT and location in {AT_SUPPLY, ABOVE_SUPPLY}:
            return BLOCK("SUPPLY_BREAKOUT_CONFIRMED")

    # 5) Regime + Expansion Filters
    if regime_flip_block(direction):
        return BLOCK("REGIME_FLIP")
    if expansion_bias_block(direction):
        return BLOCK("EXPANSION_BIAS")

    # 6) Fake Breakout (only at zones)
    if location == AT_SUPPLY and direction == SHORT:
        if not fake_breakout.confirmed:
            return BLOCK("NO_FAKE_BREAKOUT")
        if mode == "SWING":
            return BLOCK("FAKE_BREAKOUT_SWING")
        if not (rejection.confirmed and entry_delay.confirmed):
            return BLOCK("FAKE_BREAKOUT_NEEDS_REJECTION")

    if location == AT_DEMAND and direction == LONG:
        if not fake_breakout.confirmed:
            return BLOCK("NO_FAKE_BREAKOUT")
        if mode == "SWING":
            return BLOCK("FAKE_BREAKOUT_SWING")
        if not (rejection.confirmed and entry_delay.confirmed):
            return BLOCK("FAKE_BREAKOUT_NEEDS_REJECTION")

    # 7) Breakout Acceptance
    if mode == "SWING":
        if direction == LONG and not breakout_ok:
            return BLOCK("NO_SUPPLY_BREAKOUT")
        if direction == SHORT and not breakout_ok:
            return BLOCK("NO_DEMAND_BREAKOUT")
        if location == MID_RANGE:
            return BLOCK("MID_RANGE_SWING")
    else:
        if direction == LONG and location == AT_SUPPLY and not breakout_ok:
            return BLOCK("NO_SUPPLY_BREAKOUT")

    # 8) Rejection Score
    if rejection exists at zone:
        min_score = 6 if SWING else 4
        if rejection.score < min_score:
            return BLOCK("REJECTION_WEAK")

    # 9) Entry Delay
    if entry_delay.state != CONFIRMED:
        return BLOCK("ENTRY_DELAY_WAIT")

    # 10) Pullback (strong bear case)
    if market_state == STRONG_BEAR and direction == SHORT:
        if pullback missing: return BLOCK("PULLBACK_MISSING")
        if pullback.state == OVEREXTENDED and not (rejection+entry_delay):
            return BLOCK("PULLBACK_OVEREXTENDED")
        if pullback.state != HEALTHY_PULLBACK:
            return BLOCK("PULLBACK_NOT_READY")

    # 11) Indicators (soft in CASHFLOW, hard in SWING)
    if mode != "CASHFLOW" and indicator_score < 2:
        return BLOCK("WEAK_INDICATORS")

    return ACCEPT("SIGNAL_ACCEPTED")

Notes
- The zone resolution engine requires closed candles and ignores wick-only breaks.
- Breakout acceptance uses body >= 60% and a confirming candle.
