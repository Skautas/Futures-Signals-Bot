from dataclasses import dataclass


@dataclass
class CashflowTPSL:
    entry: float
    stop: float
    direction: str              # "LONG" | "SHORT"
    maker_fee_pct: float = 0.0002
    taker_fee_pct: float = 0.0005

    # CASHFLOW PARAMS
    tp_r: float = 0.5            # main TP = 0.5R
    be_r: float = 0.25           # move SL to BE after 0.25R
    min_net_rr: float = 0.30     # after fees


def calculate_tp_sl(cfg: CashflowTPSL):
    """
    Returns:
        dict with SL, TP, BE_TRIGGER, NET_RR
    """

    risk = abs(cfg.entry - cfg.stop)
    if risk <= 0:
        raise ValueError("Invalid SL distance")

    # Gross TP
    if cfg.direction == "LONG":
        tp = cfg.entry + risk * cfg.tp_r
    else:
        tp = cfg.entry - risk * cfg.tp_r

    # Fees estimation (entry + exit)
    entry_fee = cfg.entry * cfg.taker_fee_pct
    exit_fee = tp * cfg.taker_fee_pct
    total_fees = entry_fee + exit_fee

    gross_reward = abs(tp - cfg.entry)
    net_reward = gross_reward - total_fees

    net_rr = net_reward / risk

    if net_rr < cfg.min_net_rr:
        return None  # BLOCK trade (not worth it)

    # BE trigger
    if cfg.direction == "LONG":
        be_trigger = cfg.entry + risk * cfg.be_r
    else:
        be_trigger = cfg.entry - risk * cfg.be_r

    return {
        "sl": cfg.stop,
        "tp": tp,
        "be_trigger": be_trigger,
        "net_rr": round(net_rr, 3)
    }
