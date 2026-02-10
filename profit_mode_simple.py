"""
SIMPLE PROFIT MODE FOR FUTURES BOT
Minimalus pakeitimų rinkinys, kuris transformuoja botą iš FUND į PROFIT mode
"""

# ==================== KONFIGŪRACIJA ====================

try:
    from config import (
        PROFIT_MODE_ENABLED,
        PROFIT_DAILY_TARGET_EUR,
        PROFIT_MAX_TRADES_PER_DAY as CONFIG_PROFIT_MAX_TRADES_PER_DAY
    )
except ImportError:
    # Fallback jei config.py neegzistuoja
    PROFIT_MODE_ENABLED = True
    PROFIT_DAILY_TARGET_EUR = 15.0
    CONFIG_PROFIT_MAX_TRADES_PER_DAY = 6

# PELNO TIKSLAI (atsižvelgiant į mokesčius)
DAILY_NET_TARGET_EUR = PROFIT_DAILY_TARGET_EUR  # € gryno pelno per dieną
WEEKLY_NET_TARGET_EUR = 75.0  # € gryno pelno per savaitę

# RELAKSUOTI FILTRAI (sumažinami slenksčiai)
PROFIT_MODE_MIN_SCORE = 50           # Sumažinta nuo ~60
PROFIT_MODE_MIN_CONFIDENCE = 0.65    # Sumažinta nuo ~0.75

# TRADING LIMITAI (padidinti)
PROFIT_MAX_TRADES_PER_DAY = CONFIG_PROFIT_MAX_TRADES_PER_DAY        # Padidinta nuo ~3-4
PROFIT_MAX_POSITIONS = 5             # Padidinta nuo 3

# RIZIKOS VALDYMAS
PROFIT_POSITION_SIZE_MULTIPLIER = 1.3  # Padidinti pozicijas
PROFIT_DAILY_LOSS_LIMIT_PCT = 1.5      # Sumažinta nuo 2.0%

# STRATEGIJŲ AKTYVACIJA
PROFIT_ENABLED_STRATEGIES = [
    "BREAKOUT",
    "PULLBACK", 
    "SCALP_REBOUND",
    "TREND_CONTINUATION",
    "COUNTER_TREND"  # Įjungta profit mode
]

# ==================== PROFIT TRACKERIS ====================

class SimpleProfitTracker:
    """Paprastas pelno sekimas su mokesčių skaičiavimu"""
    
    def __init__(self):
        self.reset_daily()
        
    def reset_daily(self):
        """Reset'inti dienos skaitiklius"""
        self.daily_trades = 0
        self.daily_gross = 0.0
        self.daily_fees = 0.0
        self.daily_net = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
        
    def add_trade_result(self, gross_profit_eur: float, position_size_eur: float = 1000):
        """
        Pridėti trade rezultatą
        
        Args:
            gross_profit_eur: Grynas pelnas (prieš mokesčius)
            position_size_eur: Pozicijos dydis mokesčiams skaičiuoti
        """
        self.daily_trades += 1
        self.daily_gross += gross_profit_eur
        
        # Apskaičiuoti mokesčius (0.1% per round trip)
        trade_fee = position_size_eur * 0.001
        self.daily_fees += trade_fee
        self.daily_net = self.daily_gross - self.daily_fees
        
        # Update win/loss counters
        if gross_profit_eur > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        # Spausdinti statusą
        self.print_status()
    
    def should_trade_more(self) -> tuple:
        """Ar reikia dar trade'inti šiandien?"""
        # Patikrinti ar pasiekėme targetą
        if self.daily_net >= DAILY_NET_TARGET_EUR:
            return False, f"🎯 Daily target reached: {self.daily_net:.2f}€"
        
        # Patikrinti trade limitą
        if self.daily_trades >= PROFIT_MAX_TRADES_PER_DAY:
            return False, f"📊 Max trades reached: {self.daily_trades}"
        
        return True, f"Continue ({self.daily_trades}/{PROFIT_MAX_TRADES_PER_DAY} trades)"
    
    def get_win_rate(self) -> float:
        """Gauti win rate"""
        if self.daily_trades == 0:
            return 0.0
        return self.winning_trades / self.daily_trades
    
    def print_status(self):
        """Spausdinti dabartinį statusą"""
        win_rate = self.get_win_rate()
        
        print(f"\n💰 PROFIT STATUS:")
        print(f"   Trades: {self.daily_trades}/{PROFIT_MAX_TRADES_PER_DAY}")
        print(f"   Win Rate: {win_rate:.1%} ({self.winning_trades}W/{self.losing_trades}L)")
        print(f"   Gross: {self.daily_gross:.2f}€")
        print(f"   Fees: {self.daily_fees:.2f}€")
        print(f"   Net: {self.daily_net:.2f}€ / {DAILY_NET_TARGET_EUR}€")
        
        # Progress bar
        progress = min(self.daily_net / DAILY_NET_TARGET_EUR, 1.0)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = "█" * filled + "░" * (bar_length - filled)
        print(f"   Progress: [{bar}] {progress:.0%}")
        
        if self.daily_net >= DAILY_NET_TARGET_EUR:
            print(f"   ✅ DAILY NET TARGET ACHIEVED!")

# ==================== ENTRY FLOW MODIFIKATORIUS ====================

def adjust_entry_for_profit_mode(market_context: dict) -> tuple:
    """
    Pakoreguoti entry kriterijus profit mode
    
    Returns:
        (allowed: bool, reason: str, position_multiplier: float)
    """
    if not PROFIT_MODE_ENABLED:
        return True, "FUND_MODE", 1.0
    
    position_multiplier = 1.0
    reason = "ENTRY_APPROVED_PROFIT"
    
    # 1. LEISTI RANGE rinkoje (sumažinus riziką)
    regime = market_context.get('regime', 'BULL')
    if regime == "RANGE":
        position_multiplier = 0.7
        reason = "ALLOWED_IN_RANGE_REDUCED_RISK"
        return True, reason, position_multiplier
    
    if regime == "CHAOTIC":
        return False, "CHAOTIC_BLOCK", 1.0
    
    # 2. LEISTI COUNTER-TREND (sumažinus riziką)
    trend_4h = market_context.get('trend_4h', 'BULL')
    direction = market_context.get('direction', 'LONG')
    
    is_counter_trend = (
        (trend_4h in ['STRONG_BEAR', 'BEAR'] and direction == 'LONG') or
        (trend_4h in ['STRONG_BULL', 'BULL'] and direction == 'SHORT')
    )
    
    if is_counter_trend:
        position_multiplier = 0.7
        reason = "COUNTER_TREND_REDUCED_RISK"
        return True, reason, position_multiplier
    
    # 3. LEISTI AUKŠTESNĮ RSI
    rsi = market_context.get('rsi', 50)
    if 75 < rsi <= 80:
        position_multiplier = 0.6
        reason = "HIGH_RSI_REDUCED_RISK"
        return True, reason, position_multiplier
    elif rsi > 80:
        return False, "RSI_TOO_HIGH", 1.0
    
    # 4. LEISTI DIDESNĘ VOLATILITĄ
    impulse_atr_mult = market_context.get('impulse_atr_mult', 1.0)
    if 2.5 < impulse_atr_mult <= 3.0:
        position_multiplier = 0.6
        reason = "HIGH_VOLATILITY_REDUCED_RISK"
        return True, reason, position_multiplier
    elif impulse_atr_mult > 3.0:
        return False, "VOLATILITY_TOO_HIGH", 1.0
    
    return True, reason, position_multiplier

# ==================== INTEGRACIJA SU JŪSŲ BOTU ====================

"""
KAIP NAUDOTI:

1. Įdėkite šį failą į jūsų projekto aplanką
2. Pridėkite šias eilutes į futures_signals.py pradžioje:
"""

# EXAMPLE INTEGRATION CODE (įdėkite į futures_signals.py):

"""
# ===== PROFIT MODE INTEGRATION =====
try:
    from profit_mode_simple import (
        PROFIT_MODE_ENABLED,
        PROFIT_MODE_MIN_SCORE,
        PROFIT_MODE_MIN_CONFIDENCE,
        PROFIT_MAX_TRADES_PER_DAY,
        PROFIT_MAX_POSITIONS,
        SimpleProfitTracker,
        adjust_entry_for_profit_mode,
        DAILY_NET_TARGET_EUR
    )
    
    # Inicializuoti profit trackerį
    profit_tracker = SimpleProfitTracker()
    
    # Pakeisti globalius parametrus jei profit mode įjungtas
    if PROFIT_MODE_ENABLED:
        print("✅ PROFIT MODE ENABLED")
        print(f"   Daily target: {DAILY_NET_TARGET_EUR}€ net")
        print(f"   Max trades: {PROFIT_MAX_TRADES_PER_DAY}")
        
        # Override some global settings
        MIN_SCORE = PROFIT_MODE_MIN_SCORE
        MIN_CONFIDENCE = PROFIT_MODE_MIN_CONFIDENCE
        AUTO_TRADE_MAX_POSITIONS = PROFIT_MAX_POSITIONS
        
except ImportError:
    PROFIT_MODE_ENABLED = False
    print("⚠️ Profit mode module not found, using FUND mode")
"""

"""
3. Modifikuokite entry flow logiką:
"""

"""
# Raskite entry_flow.py kvietimą ir modifikuokite:

# Originalus kodas (tikriausiai):
allowed, reason = await evaluate_entry(market_context)

# Pakeiskite į:
if PROFIT_MODE_ENABLED:
    # Patikrinti ar galime dar trade'inti
    can_trade, trade_reason = profit_tracker.should_trade_more()
    if not can_trade:
        print(f"⏸️ {trade_reason}")
        continue
    
    # Pakoreguoti entry profit mode
    profit_allowed, profit_reason, position_mult = adjust_entry_for_profit_mode(market_context)
    
    if profit_allowed:
        # Adjust position size
        if 'position_size' in signal:
            signal['position_size'] *= position_mult
            signal['profit_mode_adjustment'] = position_mult
        
        print(f"✅ Profit mode: {profit_reason} (mult: {position_mult:.2f}x)")
        # Tęsti su trade
    else:
        print(f"⛔ Profit mode: {profit_reason}")
        continue
else:
    # Original FUND mode
    allowed, reason = await evaluate_entry(market_context)
    if not allowed:
        print(f"⛔ FUND mode: {reason}")
        continue
"""

"""
4. Pridėkite trade result tracking:
"""

"""
# Po kiekvieno trade'o, pridėkite:
if PROFIT_MODE_ENABLED:
    profit_tracker.add_trade_result(
        gross_profit_eur=trade_profit,
        position_size_eur=position_size
    )
"""

# ==================== TESTAVIMAS ====================

if __name__ == "__main__":
    print("🧪 Testing Profit Mode Simple")
    print("=" * 50)
    
    # Test profit tracker
    tracker = SimpleProfitTracker()
    
    print("\n1. Adding test trades:")
    tracker.add_trade_result(3.5, 1000)   # Win
    tracker.add_trade_result(-1.0, 1000)  # Loss
    tracker.add_trade_result(2.8, 1000)   # Win
    tracker.add_trade_result(4.2, 1000)   # Win
    
    print("\n2. Testing should_trade_more:")
    can_trade, reason = tracker.should_trade_more()
    print(f"   Can trade: {can_trade} - {reason}")
    
    print("\n3. Testing entry adjustments:")
    test_context = {
        'regime': 'RANGE',
        'rsi': 77,
        'trend_4h': 'BULL',
        'direction': 'LONG'
    }
    
    allowed, reason, multiplier = adjust_entry_for_profit_mode(test_context)
    print(f"   Allowed: {allowed}")
    print(f"   Reason: {reason}")
    print(f"   Multiplier: {multiplier}")
    
    print("\n✅ Profit mode ready for integration!")
