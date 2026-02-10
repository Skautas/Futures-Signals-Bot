from datetime import datetime, timezone


class DailyReport:
    """Daily trading summary for Telegram."""

    @staticmethod
    async def send_daily_summary(profit_tracker, asset_performance, daily_target, weekly_target, send_func):
        now = datetime.now(timezone.utc)
        next_reset_hours = 24 - now.hour

        report = (
            "<b>DAILY TRADING REPORT</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"<b>Profit:</b> {profit_tracker.daily_profit:.2f}€ / {daily_target:.2f}€\n"
            f"<b>Weekly:</b> {profit_tracker.weekly_profit:.2f}€ / {weekly_target:.2f}€\n"
            f"<b>Trades:</b> {profit_tracker.daily_trades}\n"
            f"<b>Win Streak:</b> {profit_tracker.consecutive_wins}\n"
            "━━━━━━━━━━━━━━━━\n"
            "<b>TOP ASSETS:</b>\n"
        )

        best_assets = asset_performance.get_best_assets()
        if best_assets:
            for asset, win_rate, avg_profit in best_assets:
                report += f"• {asset}: {win_rate:.1f}% WR, {avg_profit:.2f}€ avg\n"
        else:
            report += "• No data yet\n"

        report += (
            "━━━━━━━━━━━━━━━━\n"
            f"<b>Next reset in:</b> {next_reset_hours}h\n"
        )

        await send_func(report)
