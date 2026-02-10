"""
Strategy Health Engine Module
Monitors and manages strategy performance and health states
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class StrategyHealthState:
    strategy: str
    status: str                     # ACTIVE / WARNING / DISABLED
    win_rate: float
    expectancy: float
    trades_count: int
    consecutive_losses: int
    last_updated: datetime
    disabled_until: datetime | None
    reason: str


class StrategyHealthEngine:
    """Strategy Health Engine - monitors and manages strategy performance"""

    HARD_DISABLE_LOSSES = 5
    HARD_DISABLE_EXPECTANCY = -0.10
    WARNING_EXPECTANCY = 0.0
    WARNING_WINRATE = 0.35

    MIN_TRADES_FOR_EVAL = 10
    DISABLE_COOLDOWN_HOURS = 24

    def __init__(self):
        self.states = {}  # strategy_name -> StrategyHealthState

    def _set_state(self, strategy: str, status: str, reason: str, disabled_until: Optional[datetime] = None) -> StrategyHealthState:
        """
        Centralized state setter - creates and stores a strategy health state
        
        Args:
            strategy: Name of the strategy
            status: ACTIVE / WARNING / DISABLED
            reason: Reason for the status
            disabled_until: Optional datetime when strategy can be re-enabled
        
        Returns:
            StrategyHealthState object
        """
        state = StrategyHealthState(
            strategy=strategy,
            status=status,
            win_rate=0,
            expectancy=0,
            trades_count=0,
            consecutive_losses=0,
            last_updated=datetime.utcnow(),
            disabled_until=disabled_until,
            reason=reason
        )
        self.states[strategy] = state
        return state

    def evaluate_strategy(self, strategy_name: str) -> StrategyHealthState:
        """
        Evaluate strategy health based on recent trades
        
        Args:
            strategy_name: Strategy name from STRATEGY_LIST
        
        Returns:
            StrategyHealthState with current health status
        """
        # Import here to avoid circular imports
        from futures_signals import get_recent_trades, calculate_metrics
        
        trades = get_recent_trades(strategy_name)

        if len(trades) < self.MIN_TRADES_FOR_EVAL:
            return self._set_state(strategy_name, "ACTIVE", "INSUFFICIENT_DATA")

        metrics = calculate_metrics(trades)
        if not metrics:
            return self._set_state(strategy_name, "ACTIVE", "NO_DATA")

        win_rate, expectancy, cons_losses = metrics
        now = datetime.utcnow()

        # 🔴 HARD DISABLE
        if cons_losses >= self.HARD_DISABLE_LOSSES or expectancy <= self.HARD_DISABLE_EXPECTANCY:
            state = self._set_state(
                strategy_name,
                "DISABLED",
                f"HARD_DISABLE: losses={cons_losses}, exp={expectancy:.2f}",
                disabled_until=now + timedelta(hours=self.DISABLE_COOLDOWN_HOURS)
            )
            # Update metrics
            state.win_rate = win_rate
            state.expectancy = expectancy
            state.trades_count = len(trades)
            state.consecutive_losses = cons_losses
            return state

        # 🟡 WARNING
        if expectancy < self.WARNING_EXPECTANCY or win_rate < self.WARNING_WINRATE:
            state = self._set_state(
                strategy_name,
                "WARNING",
                f"WARNING: WR={win_rate:.0%}, EXP={expectancy:.2f}"
            )
            # Update metrics
            state.win_rate = win_rate
            state.expectancy = expectancy
            state.trades_count = len(trades)
            state.consecutive_losses = cons_losses
            return state

        # 🟢 ACTIVE
        state = self._set_state(strategy_name, "ACTIVE", "HEALTHY")
        # Update metrics
        state.win_rate = win_rate
        state.expectancy = expectancy
        state.trades_count = len(trades)
        state.consecutive_losses = cons_losses
        return state

