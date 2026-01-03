"""
Net Profit Engine Module
Calculate if TP makes sense after fees
"""
from dataclasses import dataclass


@dataclass
class NetProfitDecision:
    allow_tp: bool
    estimated_net_profit_usd: float
    fees_usd: float
    min_rr_required: float
    reason: str


def net_profit_engine(
    position_size_usd: float,
    rr_target: float,
    risk_usd: float
) -> NetProfitDecision:
    """Calculate net profit after fees and determine if TP is worth it"""
    # Kraken fees: 0.02% maker, 0.05% taker
    KRAKEN_MAKER_FEE = 0.0002
    KRAKEN_TAKER_FEE = 0.0005
    AVERAGE_FEE = (KRAKEN_MAKER_FEE + KRAKEN_TAKER_FEE) / 2
    
    # Round-trip fees (open + close)
    fees_usd = position_size_usd * AVERAGE_FEE * 2
    
    # Calculate profit
    profit_usd = risk_usd * rr_target
    
    # Net profit after fees
    net_profit = profit_usd - fees_usd
    
    # Minimum RR to cover fees + small profit
    min_rr = (fees_usd / risk_usd) + 0.1 if risk_usd > 0 else 1.5
    
    # Allow if net profit is positive
    allow = net_profit > 0 and rr_target >= min_rr
    
    return NetProfitDecision(
        allow_tp=allow,
        estimated_net_profit_usd=net_profit,
        fees_usd=fees_usd,
        min_rr_required=min_rr,
        reason="FEE_CHECK" if allow else f"FEES_TOO_HIGH (need RR >= {min_rr:.2f})"
    )

