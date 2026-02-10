from datetime import datetime


class SundayTradingConfig:
    """Sunday trading configuration (UTC hours)."""

    ENABLED = True
    SATURDAY_TRADING = True
    ALLOW_ALL_ASSETS = True

    SUNDAY_TRADING_HOURS = {
        "active": False,
        "start_hour": 8,
        "end_hour": 22,
    }

    RISK_ADJUSTMENTS = {
        "position_size_multiplier": 0.7,
        "max_positions": 2,
        "daily_loss_limit_pct": 1.5,
        "leverage_multiplier": 0.8,
    }

    ENABLED_STRATEGIES = [
        "BREAKOUT",
        "PULLBACK",
        "SCALP_REBOUND",
    ]

    PREFERRED_ASSETS = ["BTC", "ETH", "SOL"]
    MAX_ASSETS = 3

    SAFETY = {
        "auto_stop_after_losses": 3,
        "min_volume_ratio": 0.4,
        "max_volatility_multiplier": 2.0,
    }

    NOTIFICATIONS = {
        "sunday_start": True,
        "sunday_end": True,
        "hourly_updates": False,
    }


class SundayTradingEngine:
    """Controls Sunday trading rules."""

    def __init__(self, config: SundayTradingConfig = None):
        self.config = config or SundayTradingConfig()
        self.sunday_stats = {
            "trades_today": 0,
            "profit_today": 0.0,
            "consecutive_losses": 0,
            "assets_traded": set(),
        }

    def is_sunday_trading_time(self, current_time: datetime = None):
        if not self.config.ENABLED:
            return False, "Sunday trading disabled"

        current_time = current_time or datetime.utcnow()
        current_day = current_time.weekday()  # 0=Mon, 5=Sat, 6=Sun
        current_hour = current_time.hour

        if current_day == 5 and not self.config.SATURDAY_TRADING:
            return False, "Saturday - trading disabled"

        if current_day == 6:
            if self.config.SUNDAY_TRADING_HOURS["active"]:
                start = self.config.SUNDAY_TRADING_HOURS["start_hour"]
                end = self.config.SUNDAY_TRADING_HOURS["end_hour"]
                if start <= current_hour < end:
                    return True, f"Sunday {current_hour}:00 UTC - trading enabled"
                return False, f"Sunday {current_hour}:00 UTC - outside hours"
            return True, "Sunday trading enabled (all day)"

        return True, "Weekday - trading enabled"

    def adjust_for_sunday(self, trade_params: dict) -> dict:
        allowed, reason = self.is_sunday_trading_time()
        if not allowed or "Sunday" not in reason:
            return trade_params

        adjusted = trade_params.copy()
        if "position_size" in adjusted:
            adjusted["position_size"] *= self.config.RISK_ADJUSTMENTS["position_size_multiplier"]
        if "leverage" in adjusted:
            adjusted["leverage"] *= self.config.RISK_ADJUSTMENTS["leverage_multiplier"]
        adjusted["is_sunday_trade"] = True
        adjusted["sunday_adjustment"] = self.config.RISK_ADJUSTMENTS["position_size_multiplier"]
        return adjusted

    def filter_strategies_for_sunday(self, strategies):
        allowed, reason = self.is_sunday_trading_time()
        if not allowed or "Sunday" not in reason:
            return strategies
        return [s for s in strategies if s in self.config.ENABLED_STRATEGIES]

    def filter_assets_for_sunday(self, assets):
        return assets

    def update_sunday_stats(self, trade_result: dict):
        allowed, reason = self.is_sunday_trading_time()
        if not allowed or "Sunday" not in reason:
            return

        self.sunday_stats["trades_today"] += 1
        self.sunday_stats["profit_today"] += trade_result.get("profit", 0)
        if "asset" in trade_result:
            self.sunday_stats["assets_traded"].add(trade_result["asset"])

        if trade_result.get("profit", 0) <= 0:
            self.sunday_stats["consecutive_losses"] += 1
        else:
            self.sunday_stats["consecutive_losses"] = 0

    def reset_daily_stats(self):
        if datetime.utcnow().weekday() != 6:
            self.sunday_stats = {
                "trades_today": 0,
                "profit_today": 0.0,
                "consecutive_losses": 0,
                "assets_traded": set(),
            }

    def get_sunday_status(self):
        allowed, reason = self.is_sunday_trading_time()
        return {
            "is_sunday_trading": allowed,
            "reason": reason,
            "current_time_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "stats": {
                "trades_today": self.sunday_stats["trades_today"],
                "profit_today": self.sunday_stats["profit_today"],
                "consecutive_losses": self.sunday_stats["consecutive_losses"],
                "assets_traded": list(self.sunday_stats["assets_traded"]),
            },
            "config": {
                "enabled": self.config.ENABLED,
                "trading_hours": f"{self.config.SUNDAY_TRADING_HOURS['start_hour']}:00-{self.config.SUNDAY_TRADING_HOURS['end_hour']}:00 UTC",
                "risk_multiplier": self.config.RISK_ADJUSTMENTS["position_size_multiplier"],
            },
        }
