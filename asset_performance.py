class AssetPerformance:
    """Track which assets are most profitable."""

    def __init__(self):
        self.stats = {}

    def update(self, asset, profit_eur):
        if asset not in self.stats:
            self.stats[asset] = {
                'trades': 0,
                'total_profit': 0.0,
                'wins': 0,
                'losses': 0
            }

        self.stats[asset]['trades'] += 1
        self.stats[asset]['total_profit'] += profit_eur

        if profit_eur > 0:
            self.stats[asset]['wins'] += 1
        else:
            self.stats[asset]['losses'] += 1

    def get_best_assets(self, min_trades=3):
        """Return top assets by win rate."""
        ranked = []
        for asset, data in self.stats.items():
            if data['trades'] >= min_trades:
                win_rate = data['wins'] / data['trades'] * 100
                avg_profit = data['total_profit'] / data['trades']
                ranked.append((asset, win_rate, avg_profit))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:3]
