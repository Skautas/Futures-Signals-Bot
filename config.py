"""
Centralizuota konfigūracija
"""

# KRAKEN OBSERVER MODE (read-only, no auto-trading)
# When True: bot fetches positions, balance, tracks manual trades, records statistics
# Bot NEVER places orders - only observes and collects data
KRAKEN_OBSERVER_MODE = True

# TRAILING STOP ON EXCHANGE
# When True: bot places and updates SL orders on Kraken for trailing stop management
# Works in Observer mode - enables trailing for manually opened positions
TRAILING_STOP_ON_EXCHANGE = True

# TRAILING MODELS (CASHFLOW vs SWING) - must match TRADE_MODE in futures_signals.py
# CASHFLOW: greitas pinigų paėmimas, agresyvus trailing
# SWING: leidžiam rinkai dirbti, trailing tik po TP2
TRAILING_MODEL = {
    "CASHFLOW": {
        "activation_pct": 0.9,      # Activate trailing after 0.9% profit (0.8-1.0)
        "distance_min": 0.8,         # Min trail distance %
        "distance_max": 1.2,         # Max trail distance % (dynamic: more profit = wider)
        "breakeven_at": "TP1",       # Breakeven immediately when TP1 hit
        "breakeven_buffer_pct": 0.0, # No buffer for CASHFLOW
    },
    "SWING": {
        "activation_at": "TP2",      # Trailing ONLY after TP2 is hit
        "distance_pct": 3.0,         # 2.5-3.5% trail distance
        "breakeven_at": "TP1_BUFFERED",
        "breakeven_buffer_pct": 0.2, # Entry + 0.2% buffer when TP1 hit
    },
}

# PROFIT MODE
PROFIT_MODE_ENABLED = True
PROFIT_DAILY_TARGET_EUR = 15.0
PROFIT_MAX_TRADES_PER_DAY = 6
PROFIT_POSITION_MULTIPLIER = 1.3

# BEAR MARKET
BEAR_MARKET = {
    "FORCE_ACTIVATE": False,
    "STRENGTH": 85.0,
    "OVERRIDE_AUTO_DETECT": False,
    "MANUAL_OVERRIDE_LOCK": False
}

# RISK MANAGEMENT
RISK = {
    "CRITICAL_ALERT_COOLDOWN": 3600,
    "DAILY_LOSS_LIMIT_PCT": 1.5,
    "WEEKLY_LOSS_LIMIT_PCT": 5.0,
    "MAX_POSITIONS": 5
}

# TRADING HOURS
TRADING_HOURS_ENABLED = False  # Išjungta testavimui

# ZONE RESOLUTION
ZONE_RESOLUTION_MIN_BODY_PCT = 0.6
ZONE_RESOLUTION_REQUIRED_CLOSES = 2

# ENTRY RELAXATION (more signals, higher risk)
# RANGE: allow entries without zone confirmation when price is OUTSIDE any zone
RANGE_ALLOW_OUTSIDE_ZONE = True
# Zone NEAR: block when confidence >= this (per mode: SWING 65, CASHFLOW 80)
ZONE_NEAR_BLOCK_CONFIDENCE = {
    "SWING": 65,
    "CASHFLOW": 80,
}
# Fake breakout: minimum score to confirm (5=strict, 4=more entries)
FAKE_BREAKOUT_MIN_SCORE = 4
# Zone resolution: softer body req for breakout (0.6=strict, 0.5=more confirms)
ZONE_RESOLUTION_RELAXED_BODY_PCT = 0.5
