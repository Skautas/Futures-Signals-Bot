def zone_gate_decision(
    zone_state: str,  # INSIDE / NEAR / OUTSIDE
    zone_confidence: int,
    resolution_confirmed: bool,
):
    """
    Score-based gate for zones.
    """
    if zone_state == "INSIDE":
        return False, "PRICE_INSIDE_ZONE"

    if zone_state == "NEAR":
        if zone_confidence >= 60:
            return False, f"STRONG_ZONE_CONFIDENCE={zone_confidence}"
        if 40 <= zone_confidence < 60 and not resolution_confirmed:
            return False, f"ZONE_NEEDS_CONFIRMATION score={zone_confidence}"

    return True, f"ZONE_OK score={zone_confidence}"
