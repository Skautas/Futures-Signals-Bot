class AutoAdjustTargets:
    """Automatically adjust targets based on performance."""

    def __init__(self, base_daily_target=10):
        self.base_daily_target = base_daily_target
        self.current_target = base_daily_target
        self.performance_history = []

    def update(self, daily_profit):
        self.performance_history.append(daily_profit)
        if len(self.performance_history) > 5:
            self.performance_history.pop(0)

        if len(self.performance_history) >= 3:
            if all(p >= self.current_target for p in self.performance_history[-3:]):
                self.current_target = min(
                    self.current_target * 1.2,
                    self.base_daily_target * 2
                )
                print(f"🎯 Increased daily target to {self.current_target:.1f}€")
            elif all(p < self.current_target * 0.7 for p in self.performance_history[-3:]):
                self.current_target = max(
                    self.current_target * 0.8,
                    self.base_daily_target * 0.5
                )
                print(f"⚠️ Decreased daily target to {self.current_target:.1f}€")
