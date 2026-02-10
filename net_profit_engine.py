"""
Net Profit Engine Module v2.0
Calculate if TP makes sense after fees with automatic RR adjustment
"""
from dataclasses import dataclass


@dataclass
class FeeConfig:
    """Trading fee configuration"""
    taker_fee: float = 0.0005   # 0.05% taker fee
    maker_fee: float = 0.0002   # 0.02% maker fee


@dataclass
class NetProfitContext:
    """Context for net profit calculation"""
    position_size_usd: float
    risk_usd: float
    rr_target: float
    fee_config: FeeConfig
    min_net_profit_usd: float = 0.50  # FUND minimum


@dataclass
class NetProfitResult:
    """Result of net profit optimization"""
    adjusted_rr: float
    valid: bool
    reason: str
    estimated_net_profit_usd: float = 0.0
    fees_usd: float = 0.0
    min_rr_required: float = 0.0


@dataclass
class NetProfitDecision:
    """Legacy dataclass for backward compatibility"""
    allow_tp: bool
    estimated_net_profit_usd: float
    fees_usd: float
    min_rr_required: float
    reason: str


def optimize_rr_after_fees(ctx: NetProfitContext) -> NetProfitResult:
    """
    Optimize R:R ratio after fees calculation.
    
    If TP is too small after fees, automatically adjusts RR to meet minimum profit.
    
    Args:
        ctx: NetProfitContext with position size, risk, RR target, and fee config
    
    Returns:
        NetProfitResult with adjusted RR and validation status
    """
    # Calculate fees (using taker fee for worst case)
    entry_fee = ctx.position_size_usd * ctx.fee_config.taker_fee
    exit_fee = ctx.position_size_usd * ctx.fee_config.taker_fee
    total_fees = entry_fee + exit_fee
    
    # Calculate gross profit
    gross_profit = ctx.risk_usd * ctx.rr_target
    net_profit = gross_profit - total_fees
    
    # Minimum RR to cover fees + minimum profit
    min_rr = (ctx.min_net_profit_usd + total_fees) / ctx.risk_usd if ctx.risk_usd > 0 else 1.5
    
    # Check if current RR is sufficient
    if net_profit >= ctx.min_net_profit_usd:
        return NetProfitResult(
            adjusted_rr=ctx.rr_target,
            valid=True,
            reason="TP_OK_AFTER_FEES",
            estimated_net_profit_usd=net_profit,
            fees_usd=total_fees,
            min_rr_required=min_rr
        )
    
    # 🚨 TP per mažas – keliam RR
    required_rr = (ctx.min_net_profit_usd + total_fees) / ctx.risk_usd if ctx.risk_usd > 0 else 1.5
    
    return NetProfitResult(
        adjusted_rr=round(required_rr, 2),
        valid=False,
        reason="TP_TOO_SMALL_AFTER_FEES",
        estimated_net_profit_usd=net_profit,
        fees_usd=total_fees,
        min_rr_required=min_rr
    )


def net_profit_engine(
    position_size_usd: float,
    rr_target: float,
    risk_usd: float,
    min_net_profit_usd: float = 0.50
) -> NetProfitDecision:
    """
    Legacy function for backward compatibility.
    Calculate net profit after fees and determine if TP is worth it.
    
    Args:
        position_size_usd: Position size in USD
        rr_target: Target R:R ratio
        risk_usd: Risk amount in USD
        min_net_profit_usd: Minimum net profit required (default: $0.50)
    
    Returns:
        NetProfitDecision with allow_tp flag and details
    """
    # Use default fee config
    fee_config = FeeConfig()
    
    # Create context
    ctx = NetProfitContext(
        position_size_usd=position_size_usd,
        risk_usd=risk_usd,
        rr_target=rr_target,
        fee_config=fee_config,
        min_net_profit_usd=min_net_profit_usd
    )
    
    # Get optimized result
    result = optimize_rr_after_fees(ctx)
    
    # Convert to legacy format
    return NetProfitDecision(
        allow_tp=result.valid,
        estimated_net_profit_usd=result.estimated_net_profit_usd,
        fees_usd=result.fees_usd,
        min_rr_required=result.min_rr_required,
        reason=result.reason
    )
