"""
FUND MODE TAKE PROFIT ENGINE
Calculates TP levels based on risk-reward ratios and fee coverage
"""
# ===============================
# FUND MODE TAKE PROFIT ENGINE
# ===============================

def calculate_tp_levels(
    entry_price: float,
    stop_loss: float,
    direction: str,
    fees_pct: float = 0.0025,   # ~0.25% round-trip
    min_tp1_r: float = 0.8,
    tp2_r: float = 2.0,
    enable_tp1: bool = True
):
    """
    Returns TP1, TP2, TP3 levels or None if TP1 is not allowed.
    
    Args:
        entry_price: Entry price for the trade
        stop_loss: Stop loss price
        direction: "LONG" or "SHORT"
        fees_pct: Fee percentage (default 0.25% round-trip)
        min_tp1_r: Minimum TP1 risk-reward ratio (default 0.8)
        tp2_r: TP2 risk-reward ratio (default 2.0)
        enable_tp1: Whether to enable TP1 (can be disabled if doesn't cover fees)
    
    Returns:
        Dictionary with TP1, TP2, TP3 levels and metadata, or None if invalid
    """
    # --- Risk calculation ---
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    # --- Minimum price move to cover fees ---
    min_move_for_fees = entry_price * fees_pct

    # --- TP1 validation ---
    tp1_risk_move = risk * min_tp1_r

    # If TP1 does not even cover fees -> disable TP1
    if tp1_risk_move <= min_move_for_fees:
        enable_tp1 = False

    # --- Direction handling ---
    if direction.upper() == "LONG":
        tp1 = entry_price + tp1_risk_move if enable_tp1 else None
        tp2 = entry_price + risk * tp2_r
        tp3 = None  # handled by trailing stop
    else:
        tp1 = entry_price - tp1_risk_move if enable_tp1 else None
        tp2 = entry_price - risk * tp2_r
        tp3 = None

    return {
        "TP1": round(tp1, 5) if tp1 else None,
        "TP2": round(tp2, 5),
        "TP3": tp3,
        "TP1_ENABLED": enable_tp1,
        "MIN_RISK_R": min_tp1_r
    }

