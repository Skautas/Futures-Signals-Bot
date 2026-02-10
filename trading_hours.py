from datetime import datetime


class TradingHoursOptimizer:
    """Trade only during the most profitable hours (UTC)."""

    BEST_HOURS = {
        'BTC': [8, 9, 10, 14, 15, 16, 20, 21],
        'ETH': [9, 10, 11, 15, 16, 17, 21, 22],
        'DEFAULT': [8, 9, 10, 14, 15, 16, 20, 21, 22]
    }

    @staticmethod
    def trading_hours_penalty(asset):
        current_hour = datetime.utcnow().hour
        hours = TradingHoursOptimizer.BEST_HOURS.get(
            asset,
            TradingHoursOptimizer.BEST_HOURS['DEFAULT']
        )
        if current_hour in hours:
            return 0  # no penalty
        return -5  # soft penalty
