def zone_gate_decision(
    zone_state: str,  # INSIDE / NEAR / OUTSIDE
    zone_confidence: int,
    resolution_confirmed: bool,
    mode: str = "CASHFLOW",
):
    """
    Score-based gate for zones.
    ZONE_NEAR: SWING blocks at 65+, CASHFLOW at 80+.
    """
    try:
        from config import ZONE_NEAR_BLOCK_CONFIDENCE
    except ImportError:
        ZONE_NEAR_BLOCK_CONFIDENCE = {"SWING": 65, "CASHFLOW": 80}

    block_conf = ZONE_NEAR_BLOCK_CONFIDENCE
    if isinstance(block_conf, dict):
        block_level = block_conf.get(mode, block_conf.get("CASHFLOW", 80))
    else:
        block_level = block_conf

    if zone_state == "INSIDE":
        return False, "PRICE_INSIDE_ZONE"

    if zone_state == "NEAR":
        if zone_confidence >= block_level:
            return False, "ZONE_NEAR_STRONG"
        if 40 <= zone_confidence < block_level and not resolution_confirmed:
            return False, f"ZONE_NEEDS_CONFIRMATION score={zone_confidence}"

    return True, f"ZONE_OK score={zone_confidence}"
