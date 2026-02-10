"""
BEAR MARKET MODULE
Automatiškai koreguoja filtrus bear market sąlygoms
"""

from dataclasses import dataclass
from typing import Dict
from datetime import datetime
import statistics

# ==================== KONFIGŪRACIJA ====================


@dataclass
class BearMarketConfig:
    """Bear market konfigūracija"""
    
    # AKTYVACIJA
    ENABLED: bool = True
    AUTO_DETECT: bool = True  # Automatiškai aptikti bear market
    
    # RINKOS SĄLYGŲ APTIKIMAS
    DETECTION_PARAMS = {
        'btc_trend_threshold': -5.0,  # BTC pokytis % per 7 dienas
        'market_cap_threshold': -8.0, # Total market cap change %
        'fear_greed_threshold': 40,   # Fear & Greed Index < 40
        'bear_duration_days': 5,      # Kiek dienų turi būti bear
    }
    
    # RELAKSUOTI FILTRAI BEAR MARKET
    RELAXED_PARAMS = {
        # ENTRY FILTRAI
        'rsi_overbought_limit': 80,          # Padidinta nuo 70 (tik LONG)
        'rsi_oversold_limit': 25,           # Sumažinta nuo 30 (SHORT entry)
        'max_candle_body_pct': 1.2,         # Padidinta nuo 0.6%
        'max_atr_pct': 2.8,                 # Padidinta nuo 1.8%
        'ema_entry_distance': 0.6,          # Padidinta nuo 0.25%
        'max_ema_distance': 1.8,            # Padidinta nuo 1.2%
        
        # STRATEGIJOS
        'preferred_strategies': [
            'BREAKOUT_SHORT',
            'TREND_CONTINUATION_SHORT',
            'OVERSOLD_BOUNCE_LONG',  # Tik stipriai oversold
            'BEAR_FLAG_SHORT'
        ],
        
        # RISK MANAGEMENT
        'position_size_multiplier': 0.7,    # 70% normalios pozicijos
        'stop_loss_multiplier': 1.2,        # 20% platesnis SL
        'take_profit_multiplier': 0.8,      # 20% artimesnis TP
        'max_daily_loss_pct': 1.2,          # Sumažinta nuo 1.5%
        
        # ASSETAI
        'preferred_assets': ['BTC', 'ETH'], # Tik pagrindiniai
        'avoid_altcoins': True,             # Venkiti altcoin'ų
    }
    
    # NOTIFIKACIJOS
    NOTIFICATIONS = {
        'bear_market_entered': True,
        'filter_adjustments': True,
        'daily_summary': True,
    }

# ==================== BEAR MARKET DETECTOR ====================


class BearMarketDetector:
    """Aptinka bear market sąlygas"""
    
    def __init__(self, config: BearMarketConfig = None):
        self.config = config or BearMarketConfig()
        self.is_bear_market = False
        self.bear_strength = 0.0  # 0-100%
        self.detected_at = None
        self.manual_override = False
        self.metrics_history = []

    def set_manual_override(self, enabled: bool = True, strength: float = 85.0):
        """Rankinis override su uzraktu"""
        self.manual_override = enabled
        self.is_bear_market = enabled
        if enabled:
            self.bear_strength = strength
            self.detected_at = datetime.utcnow() - timedelta(days=3)
        
    def update_market_metrics(self, metrics: Dict):
        """Atnaujinti rinkos metrikas"""
        if self.manual_override:
            return
        self.metrics_history.append({
            'timestamp': datetime.utcnow(),
            'btc_change_7d': metrics.get('btc_change_7d', 0),
            'total_cap_change': metrics.get('total_cap_change', 0),
            'fear_greed': metrics.get('fear_greed', 50),
            'dominant_trend': metrics.get('dominant_trend', 'NEUTRAL')
        })
        
        # Laikyti tik paskutines 30 įrašų
        if len(self.metrics_history) > 30:
            self.metrics_history.pop(0)
        
        # Tikrinti bear market
        self._detect_bear_market()
    
    def _detect_bear_market(self):
        """Aptikti bear market"""
        if not self.config.ENABLED or not self.config.AUTO_DETECT:
            return
        
        if len(self.metrics_history) < 7:
            return  # Nepakankamai duomenų
        
        # Apskaičiuoti vidurkius
        recent = self.metrics_history[-7:]  # Paskutinės 7 dienos
        
        avg_btc_change = statistics.mean([m['btc_change_7d'] for m in recent])
        avg_cap_change = statistics.mean([m['total_cap_change'] for m in recent])
        avg_fear_greed = statistics.mean([m['fear_greed'] for m in recent])
        
        # Tikrinti kriterijus
        bear_signals = 0
        total_signals = 3
        
        if avg_btc_change < self.config.DETECTION_PARAMS['btc_trend_threshold']:
            bear_signals += 1
        
        if avg_cap_change < self.config.DETECTION_PARAMS['market_cap_threshold']:
            bear_signals += 1
            
        if avg_fear_greed < self.config.DETECTION_PARAMS['fear_greed_threshold']:
            bear_signals += 1
        
        # Nustatyti bear market statusą
        was_bear = self.is_bear_market
        self.is_bear_market = bear_signals >= 2  # 2 iš 3 ženklų
        self.bear_strength = (bear_signals / total_signals) * 100
        
        if self.is_bear_market and not was_bear:
            self.detected_at = datetime.utcnow()
            print(f"⚠️ BEAR MARKET DETECTED (strength: {self.bear_strength:.0f}%)")
            print(
                "🔄 REGIME SWITCH: BULL → BEAR | "
                f"BTC 7d: {avg_btc_change:.2f}% | "
                f"Cap 7d: {avg_cap_change:.2f}% | "
                f"Fear&Greed: {avg_fear_greed:.0f}"
            )
        
        elif not self.is_bear_market and was_bear:
            print("✅ BEAR MARKET ENDED")
            print(
                "🔄 REGIME SWITCH: BEAR → BULL | "
                f"BTC 7d: {avg_btc_change:.2f}% | "
                f"Cap 7d: {avg_cap_change:.2f}% | "
                f"Fear&Greed: {avg_fear_greed:.0f}"
            )
    
    def get_bear_status(self) -> Dict:
        """Gauti bear market statusą"""
        return {
            'is_bear_market': self.is_bear_market,
            'bear_strength': self.bear_strength,
            'detected_at': self.detected_at,
            'duration_days': (datetime.utcnow() - self.detected_at).days if self.detected_at else 0,
            'config': self.config.RELAXED_PARAMS if self.is_bear_market else {}
        }

# ==================== FILTER ADJUSTMENT ENGINE ====================


class BearFilterAdjuster:
    """Koreguoja filtrus bear market sąlygoms"""
    
    def __init__(self, detector: BearMarketDetector):
        self.detector = detector
        self.original_filters = {}
        
    def adjust_rsi_filter(self, rsi_value: float, direction: str) -> tuple:
        """
        Pakoreguoti RSI filtrą bear market
        
        Returns: (allowed: bool, adjusted_limit: float)
        """
        if not self.detector.is_bear_market:
            return True, 70.0  # Normalus limitas
        
        # Bear market adjustments
        if direction == "LONG":
            # LONG pozicijoms - aukštesnis RSI limitas
            adjusted_limit = self.detector.config.RELAXED_PARAMS['rsi_overbought_limit']
            allowed = rsi_value <= adjusted_limit
            return allowed, adjusted_limit
            
        # SHORT
        adjusted_limit = self.detector.config.RELAXED_PARAMS['rsi_oversold_limit']
        # SHORT strategijoms RSI gali būti žemas
        allowed = True  # Bear market dažniausiai SHORT
        return allowed, adjusted_limit
    
    def adjust_candle_body_filter(self, body_pct: float) -> tuple:
        """Pakoreguoti žvakės body filtrą"""
        if not self.detector.is_bear_market:
            max_limit = 0.6
        else:
            max_limit = self.detector.config.RELAXED_PARAMS['max_candle_body_pct']
        
        allowed = body_pct <= max_limit
        return allowed, max_limit
    
    def adjust_atr_filter(self, atr_pct: float) -> tuple:
        """Pakoreguoti ATR filtrą"""
        if not self.detector.is_bear_market:
            max_limit = 1.8
        else:
            max_limit = self.detector.config.RELAXED_PARAMS['max_atr_pct']
        
        allowed = atr_pct <= max_limit
        return allowed, max_limit
    
    def adjust_ema_filters(self, ema_distance: float) -> tuple:
        """Pakoreguoti EMA filtrus"""
        if not self.detector.is_bear_market:
            entry_limit = 0.25
            wait_limit = 1.2
        else:
            entry_limit = self.detector.config.RELAXED_PARAMS['ema_entry_distance']
            wait_limit = self.detector.config.RELAXED_PARAMS['max_ema_distance']
        
        # Nustatyti statusą pagal distance
        if ema_distance <= entry_limit:
            status = "ENTER"
            reason = f"EMA_DISTANCE_OK ({ema_distance:.2f}%)"
        elif ema_distance <= wait_limit:
            status = "ARM"
            reason = f"HIGH_EMA_DISTANCE_BEAR_MARKET ({ema_distance:.2f}%)"
        else:
            status = "WAIT"
            reason = f"TOO_FAR_FROM_EMA_BEAR ({ema_distance:.2f}%)"
        
        return status, reason, entry_limit, wait_limit
    
    def adjust_position_size(self, base_size: float) -> float:
        """Pakoreguoti pozicijos dydį"""
        if not self.detector.is_bear_market:
            return base_size
        
        multiplier = self.detector.config.RELAXED_PARAMS['position_size_multiplier']
        return base_size * multiplier
    
    def get_preferred_strategies(self) -> list:
        """Gauti pageidaujamas strategijas bear market"""
        if not self.detector.is_bear_market:
            return []  # Visos strategijos
        
        return self.detector.config.RELAXED_PARAMS['preferred_strategies']
    
    def get_preferred_assets(self, all_assets: list) -> list:
        """Gauti pageidaujamus asset'us bear market"""
        if not self.detector.is_bear_market:
            return all_assets
        
        preferred = self.detector.config.RELAXED_PARAMS['preferred_assets']
        if self.detector.config.RELAXED_PARAMS['avoid_altcoins']:
            return [a for a in all_assets if a in preferred]
        
        return all_assets

# ==================== INTEGRACIJA SU JŪSŲ BOTU ====================


class BearMarketEngine:
    """Pagrindinis bear market variklis"""
    
    def __init__(self, config: BearMarketConfig = None):
        self.config = config or BearMarketConfig()
        self.detector = BearMarketDetector(self.config)
        self.adjuster = BearFilterAdjuster(self.detector)
        
        print(f"""
🐻 BEAR MARKET ENGINE INITIALIZED
   Auto-detect: {'ENABLED' if self.config.AUTO_DETECT else 'DISABLED'}
   Current status: {'BEAR MARKET' if self.detector.is_bear_market else 'NORMAL'}
   Bear strength: {self.detector.bear_strength:.0f}%
        """)
    
    def update_market_data(self, market_data: Dict):
        """Atnaujinti rinkos duomenis"""
        self.detector.update_market_metrics(market_data)
        
        # Spausdinti statusą jei pasikeitė
        status = self.detector.get_bear_status()
        if status['is_bear_market']:
            print(f"🐻 BEAR MARKET ACTIVE (Day {status['duration_days']})")
            print(f"   Strength: {status['bear_strength']:.0f}%")
            print(f"   Position size: {self.config.RELAXED_PARAMS['position_size_multiplier']*100:.0f}%")
    
    def process_signal_filters(self, signal_data: Dict) -> Dict:
        """
        Apdoroti signalo filtrus per bear market sistemą
        
        Returns: enhanced signal with bear market adjustments
        """
        if not self.detector.is_bear_market:
            return signal_data  # Jokių pakeitimų
        
        enhanced_signal = signal_data.copy()
        enhanced_signal['bear_market'] = True
        adjustments = {}
        
        # 1. Adjust RSI filter
        rsi = signal_data.get('rsi', 50)
        direction = signal_data.get('direction', 'LONG')
        rsi_allowed, rsi_limit = self.adjuster.adjust_rsi_filter(rsi, direction)
        
        adjustments['rsi'] = {
            'original': 70.0,
            'adjusted': rsi_limit,
            'allowed': rsi_allowed,
            'value': rsi
        }
        
        # 2. Adjust candle body
        body_pct = signal_data.get('candle_body_pct', 0)
        body_allowed, body_limit = self.adjuster.adjust_candle_body_filter(body_pct)
        
        adjustments['candle_body'] = {
            'original': 0.6,
            'adjusted': body_limit,
            'allowed': body_allowed,
            'value': body_pct
        }
        
        # 3. Adjust ATR
        atr_pct = signal_data.get('atr_pct', 1.0)
        atr_allowed, atr_limit = self.adjuster.adjust_atr_filter(atr_pct)
        
        adjustments['atr'] = {
            'original': 1.8,
            'adjusted': atr_limit,
            'allowed': atr_allowed,
            'value': atr_pct
        }
        
        # 4. Adjust EMA distance
        ema_distance = signal_data.get('ema_distance', 0)
        ema_status, ema_reason, entry_limit, wait_limit = self.adjuster.adjust_ema_filters(ema_distance)
        
        adjustments['ema'] = {
            'original_entry': 0.25,
            'adjusted_entry': entry_limit,
            'original_wait': 1.2,
            'adjusted_wait': wait_limit,
            'status': ema_status,
            'reason': ema_reason,
            'value': ema_distance
        }
        
        # 5. Adjust position size
        if 'position_size' in enhanced_signal:
            original_size = enhanced_signal['position_size']
            adjusted_size = self.adjuster.adjust_position_size(original_size)
            enhanced_signal['position_size'] = adjusted_size
            
            adjustments['position_size'] = {
                'original': original_size,
                'adjusted': adjusted_size,
                'multiplier': self.config.RELAXED_PARAMS['position_size_multiplier']
            }
        
        # 6. Add all adjustments to signal
        enhanced_signal['bear_adjustments'] = adjustments
        
        # 7. Check if all filters pass
        all_filters_pass = (
            rsi_allowed and
            body_allowed and
            atr_allowed and
            ema_status != "WAIT"
        )
        
        enhanced_signal['bear_market_allowed'] = all_filters_pass
        
        if all_filters_pass:
            print(f"✅ BEAR MARKET SIGNAL: {signal_data.get('asset')} {direction}")
            print(f"   RSI: {rsi}/{rsi_limit} | Body: {body_pct}%/{body_limit}%")
            print(f"   ATR: {atr_pct}%/{atr_limit}% | EMA: {ema_status} ({ema_distance:.2f}%)")
        
        return enhanced_signal
    
    def get_filter_summary(self) -> Dict:
        """Gauti filtrų suvestinę"""
        status = self.detector.get_bear_status()
        
        if not status['is_bear_market']:
            return {
                'market_mode': 'NORMAL',
                'filters': 'Standard',
                'position_size': '100%'
            }
        
        return {
            'market_mode': 'BEAR',
            'bear_strength': status['bear_strength'],
            'duration_days': status['duration_days'],
            'filters': {
                'rsi_long_limit': self.config.RELAXED_PARAMS['rsi_overbought_limit'],
                'rsi_short_limit': self.config.RELAXED_PARAMS['rsi_oversold_limit'],
                'max_candle_body': self.config.RELAXED_PARAMS['max_candle_body_pct'],
                'max_atr': self.config.RELAXED_PARAMS['max_atr_pct'],
                'ema_entry': self.config.RELAXED_PARAMS['ema_entry_distance'],
                'ema_wait': self.config.RELAXED_PARAMS['max_ema_distance']
            },
            'risk': {
                'position_size': f"{self.config.RELAXED_PARAMS['position_size_multiplier']*100:.0f}%",
                'stop_loss': f"{self.config.RELAXED_PARAMS['stop_loss_multiplier']}x",
                'take_profit': f"{self.config.RELAXED_PARAMS['take_profit_multiplier']}x"
            },
            'strategies': self.config.RELAXED_PARAMS['preferred_strategies'],
            'assets': self.config.RELAXED_PARAMS['preferred_assets']
        }


def set_bear_manual_override(bear_engine, enabled: bool = True, strength: float = 85.0):
    """Uzrakinti bear market rankiniam override"""
    if hasattr(bear_engine, 'detector'):
        bear_engine.detector.set_manual_override(enabled, strength)
        print(f"🔒 BEAR MARKET {'LOCKED' if enabled else 'UNLOCKED'}")


# ==================== INTEGRACIJA SU FUTURES_SIGNALS.PY ====================

"""
KAIP INTEGRUOTI:

1. Pridėkite importą:
"""
# ADD TO IMPORTS:
# from bear_market_mode import BearMarketConfig, BearMarketEngine

"""
2. Inicializuokite bear market variklį:
"""
# ADD AFTER OTHER INITIALIZATIONS:
# bear_config = BearMarketConfig(
#     ENABLED=True,
#     AUTO_DETECT=True
# )
# bear_engine = BearMarketEngine(bear_config)

"""
3. Atnaujinkite rinkos duomenis periodiškai:
"""
# IN YOUR MAIN LOOP, ADD:
# market_data = {
#     'btc_change_7d': get_btc_7d_change(),  # Jūsų funkcija
#     'total_cap_change': get_market_cap_change(),
#     'fear_greed': get_fear_greed_index(),
#     'dominant_trend': get_dominant_trend()
# }
# bear_engine.update_market_data(market_data)

"""
4. Apdorokite signalus per bear market sistemą:
"""
# IN SIGNAL PROCESSING:
# enhanced_signal = bear_engine.process_signal_filters(signal_data)
#
# if enhanced_signal.get('bear_market_allowed', True):
#     # Tęsti su trade
#     pass
# else:
#     print(f"⛔ Bear market filters blocked: {enhanced_signal.get('asset')}")

"""
5. Pridėkite bear market statusą į dashboard'ą:
"""
# IN FLASK ROUTES:
# @app.route('/bear_status')
# def bear_status():
#     summary = bear_engine.get_filter_summary()
#     return jsonify(summary)


# ==================== TESTAVIMAS ====================


def test_bear_market():
    """Test bear market engine"""
    print("🧪 Testing Bear Market Engine")
    print("=" * 50)
    
    engine = BearMarketEngine()
    
    # Test signal data
    test_signal = {
        'asset': 'BTC',
        'direction': 'SHORT',
        'rsi': 35.0,
        'candle_body_pct': 0.8,
        'atr_pct': 2.2,
        'ema_distance': 0.9,
        'position_size': 1.0
    }
    
    # Simulate bear market detection
    engine.detector.is_bear_market = True
    engine.detector.bear_strength = 75.0
    
    print("\n1. Processing signal in BEAR market:")
    enhanced = engine.process_signal_filters(test_signal)
    
    print(f"\n2. Bear market adjustments:")
    for key, adj in enhanced.get('bear_adjustments', {}).items():
        print(f"   {key}: {adj}")
    
    print(f"\n3. Signal allowed: {enhanced.get('bear_market_allowed')}")
    
    print("\n4. Filter summary:")
    summary = engine.get_filter_summary()
    for key, value in summary.items():
        print(f"   {key}: {value}")


if __name__ == "__main__":
    test_bear_market()
