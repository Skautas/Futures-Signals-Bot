# xgboost_trading.py
"""
XGBoost Trading Enhancement Module
Integracija į Futures Signals Botą v8.9.24

FUNKCIJOS:
1. ML confidence boosting - pagerina signalų tikslumą
2. False signal filtering - išfiltruoja netikrus signalus
3. Dynamic position sizing - koreguoja pozicijos dydį pagal ML tikimybę
4. Feature importance tracking - rodo svarbiausius faktorius
5. Performance monitoring - seka ML modelio veikimą

INSTALIAVIMAS:
pip install xgboost scikit-learn imbalanced-learn pandas numpy joblib
"""

import xgboost as xgb
import pandas as pd
import numpy as np
import joblib
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
from dataclasses import is_dataclass, asdict
import warnings
warnings.filterwarnings('ignore')

# ==================== KONFIGŪRACIJA ====================

class XGBoostConfig:
    """XGBoost modulio konfigūracija"""
    
    # Aktyvacija
    ENABLED = True
    SHADOW_MODE = True  # Pradžioje testavimas be realių pinigų
    CONFIDENCE_BOOST_ONLY = True  # Tik confidence boosting (saugiausias)
    
    # Modelio parametrai
    MODEL_PATH = "models/xgboost_model.joblib"
    DATA_PATH = "data/ml_training_data.json"
    MIN_TRAINING_SAMPLES = 100  # Min trade'ų skaičius modeliui
    
    # Feature parametrai
    FEATURE_VERSION = "v1.0"
    
    # Prognozės parametrai
    CONFIDENCE_THRESHOLD = 0.65  # Minimali tikimybė ML
    POSITION_ADJUSTMENT_ENABLED = True
    MAX_POSITION_BOOST = 1.5  # Maksimalus padidinimas
    MIN_POSITION_REDUCTION = 0.5  # Minimalus sumažinimas
    
    # Retraining parametrai
    AUTO_RETRAIN_DAYS = 7  # Automatinis perėjimas kas savaitę
    MIN_ACCURACY_FOR_PRODUCTION = 0.60  # Minimali tikslumas produkcijai
    
    # Logging
    LOG_PREDICTIONS = True
    LOG_PATH = "logs/xgboost_predictions.json"

# ==================== FEATURE ENGINEERING ====================

class FeatureEngineer:
    """Paruošia features iš signalo ir market duomenų"""
    
    @staticmethod
    def extract_features(signal: Dict, market_data: Dict) -> Dict[str, float]:
        """
        Ištraukia features iš signalo ir rinkos duomenų
        Returns: Dict su feature reikšmėmis
        """
        features = {}
        
        # ===== TECHNICAL INDICATORS =====
        # RSI features
        features['rsi_14'] = market_data.get('rsi_14', 50)
        features['rsi_28'] = market_data.get('rsi_28', 50)
        features['rsi_position'] = (features['rsi_14'] - 30) / 40  # Normalized 30-70
        
        # MACD features
        features['macd'] = market_data.get('macd', 0)
        features['macd_signal'] = market_data.get('macd_signal', 0)
        features['macd_histogram'] = market_data.get('macd_histogram', 0)
        features['macd_trend'] = 1 if features['macd'] > features['macd_signal'] else -1
        
        # Bollinger Bands
        features['bb_position'] = market_data.get('bb_position', 0)  # 0=mid, -1=lower, 1=upper
        features['bb_width'] = market_data.get('bb_width', 0.1)
        features['bb_squeeze'] = 1 if features['bb_width'] < 0.05 else 0
        
        # Moving Averages
        features['ema_20'] = market_data.get('ema_20', 0)
        features['ema_50'] = market_data.get('ema_50', 0)
        features['ema_200'] = market_data.get('ema_200', 0)
        
        features['price_vs_ema20'] = market_data.get('price_vs_ema20', 0)
        features['price_vs_ema50'] = market_data.get('price_vs_ema50', 0)
        features['price_vs_ema200'] = market_data.get('price_vs_ema200', 0)
        
        # ATR & Volatility
        features['atr'] = market_data.get('atr', 0)
        features['atr_pct'] = market_data.get('atr_pct', 0.02)
        features['volatility_ratio'] = market_data.get('volatility_ratio', 1.0)
        
        # ===== PRICE ACTION =====
        # Support/Resistance
        features['dist_to_support_pct'] = market_data.get('dist_to_support_pct', 0.05)
        features['dist_to_resistance_pct'] = market_data.get('dist_to_resistance_pct', 0.05)
        features['support_strength'] = market_data.get('support_strength', 0.5)
        features['resistance_strength'] = market_data.get('resistance_strength', 0.5)
        
        # Candlestick patterns
        features['candle_size_pct'] = market_data.get('candle_size_pct', 1.0)
        features['candle_body_ratio'] = market_data.get('candle_body_ratio', 0.5)
        features['wick_ratio'] = market_data.get('wick_ratio', 0.3)
        
        # ===== VOLUME ANALYSIS =====
        features['volume'] = market_data.get('volume', 0)
        features['volume_ma_ratio'] = market_data.get('volume_ma_ratio', 1.0)
        features['volume_trend'] = market_data.get('volume_trend', 0)  # -1=down, 0=flat, 1=up
        features['volume_vs_avg'] = market_data.get('volume_vs_avg', 1.0)
        
        # ===== MARKET CONTEXT =====
        # BTC Dominance
        features['btc_dominance'] = market_data.get('btc_dominance', 50)
        features['btc_trend'] = market_data.get('btc_trend', 0)  # -1=bear, 0=neutral, 1=bull
        
        # Market Regime
        regime_map = {'BEAR': -1, 'NEUTRAL': 0, 'BULL': 1}
        features['market_regime'] = regime_map.get(market_data.get('market_regime', 'NEUTRAL'), 0)
        
        # Fear & Greed
        features['fear_greed'] = market_data.get('fear_greed_index', 50)
        
        # Correlation
        features['corr_btc'] = market_data.get('corr_btc', 0)
        
        # ===== SIGNAL SPECIFIC =====
        # Signal strength
        features['signal_score'] = signal.get('score', 50) / 100  # Normalize to 0-1
        features['signal_confidence'] = signal.get('confidence', 0.5)
        
        # Strategy type encoding
        strategy = signal.get('strategy', 'TREND_CONTINUATION')
        strategy_map = {
            'TREND_CONTINUATION': 1,
            'PULLBACK': 2,
            'COUNTER_TREND': 3,
            'SCALP_REBOUND': 4,
            'BREAKOUT': 5
        }
        features['strategy_type'] = strategy_map.get(strategy, 0)
        
        # Confluence
        features['confluence_score'] = signal.get('confluence_score', 0) / 100
        features['entry_quality'] = signal.get('entry_quality', 0.5)
        
        # ===== TIME FEATURES =====
        now = datetime.now()
        features['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
        features['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
        features['day_of_week'] = now.weekday() / 6  # Normalize 0-1
        features['month'] = now.month / 12  # Normalize 0-1
        
        # ===== ASSET SPECIFIC =====
        asset = signal.get('asset', 'BTC')
        asset_vol_map = {
            'BTC': 0.02, 'ETH': 0.03, 'SOL': 0.05,
            'XRP': 0.04, 'LTC': 0.035, 'ADA': 0.045,
            'DOT': 0.042, 'LINK': 0.038
        }
        features['asset_volatility'] = asset_vol_map.get(asset, 0.03)
        
        return features
    
    @staticmethod
    def prepare_feature_dataframe(features_dict: Dict) -> pd.DataFrame:
        """Konvertuoja features dict į DataFrame su teisinga tvarka"""
        # Nustatyti feature tvarką (svarbu XGBoost modeliui)
        feature_order = [
            # Technical
            'rsi_14', 'rsi_28', 'rsi_position',
            'macd', 'macd_signal', 'macd_histogram', 'macd_trend',
            'bb_position', 'bb_width', 'bb_squeeze',
            'price_vs_ema20', 'price_vs_ema50', 'price_vs_ema200',
            'atr_pct', 'volatility_ratio',
            
            # Price Action
            'dist_to_support_pct', 'dist_to_resistance_pct',
            'support_strength', 'resistance_strength',
            'candle_size_pct', 'candle_body_ratio', 'wick_ratio',
            
            # Volume
            'volume_ma_ratio', 'volume_trend', 'volume_vs_avg',
            
            # Market Context
            'btc_dominance', 'btc_trend', 'market_regime',
            'fear_greed', 'corr_btc',
            
            # Signal
            'signal_score', 'signal_confidence', 'strategy_type',
            'confluence_score', 'entry_quality',
            
            # Time
            'hour_sin', 'hour_cos', 'day_of_week', 'month',
            
            # Asset
            'asset_volatility'
        ]
        
        # Sukurti DataFrame su teisinga tvarka
        df = pd.DataFrame([features_dict])
        
        # Užpildyti trūkstamus features 0
        for feature in feature_order:
            if feature not in df.columns:
                df[feature] = 0
        
        # Grąžinti tik reikalingus stulpelius
        return df[feature_order]

# ==================== XGBOOST MODEL MANAGER ====================

class XGBoostModelManager:
    """Valdo XGBoost modelio gyvavimo ciklą"""
    
    def __init__(self, config: XGBoostConfig):
        self.config = config
        self.model = None
        self.feature_names = []
        self.model_metadata = {
            'accuracy': 0.0,
            'trained_samples': 0,
            'last_trained': None,
            'feature_version': config.FEATURE_VERSION
        }
        
        # Performance tracking
        self.prediction_history = deque(maxlen=1000)
        self.shadow_trades = []
        
        # Įkelti modelį jei egzistuoja
        self.load_model()
    
    def load_model(self):
        """Įkelti išsaugotą modelį"""
        try:
            if os.path.exists(self.config.MODEL_PATH):
                loaded_data = joblib.load(self.config.MODEL_PATH)
                self.model = loaded_data['model']
                self.feature_names = loaded_data['feature_names']
                self.model_metadata = loaded_data.get('metadata', self.model_metadata)
                print(f"✅ Įkeltas XGBoost modelis (Accuracy: {self.model_metadata['accuracy']:.2%})")
            else:
                print("ℹ️ Modelis nerastas, bus naudojamas default confidence")
        except Exception as e:
            print(f"❌ Klaida įkeliant modelį: {e}")
            self.model = None
    
    def save_model(self):
        """Išsaugoti modelį su visais duomenimis"""
        try:
            # Sukurti direktoriją jei neegzistuoja
            os.makedirs(os.path.dirname(self.config.MODEL_PATH), exist_ok=True)
            
            # Paruošti duomenis
            save_data = {
                'model': self.model,
                'feature_names': self.feature_names,
                'metadata': self.model_metadata,
                'config': {
                    'feature_version': self.config.FEATURE_VERSION,
                    'saved_at': datetime.now().isoformat()
                }
            }
            
            # Išsaugoti
            joblib.dump(save_data, self.config.MODEL_PATH)
            print(f"💾 Išsaugotas XGBoost modelis: {self.config.MODEL_PATH}")
            return True
        except Exception as e:
            print(f"❌ Klaida saugant modelį: {e}")
            return False
    
    def train_model(self, X: pd.DataFrame, y: pd.Series) -> bool:
        """Apšvieti XGBoost modelį"""
        try:
            print(f"🎯 Treniruojamas XGBoost modelis su {len(X)} mėginių...")
            
            # Patikrinti duomenis
            if len(X) < self.config.MIN_TRAINING_SAMPLES:
                print(f"❌ Nepakankamas duomenų kiekis: {len(X)} < {self.config.MIN_TRAINING_SAMPLES}")
                return False
            
            # Išsaugoti feature names
            self.feature_names = X.columns.tolist()
            
            # Split duomenis (time-series split)
            from sklearn.model_selection import TimeSeriesSplit
            tscv = TimeSeriesSplit(n_splits=5)
            
            # XGBoost parametrai
            params = {
                'n_estimators': 100,
                'max_depth': 5,
                'learning_rate': 0.1,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'objective': 'binary:logistic',
                'eval_metric': 'logloss',
                'scale_pos_weight': 2,  # Daugiau svorio positive class (winning trades)
                'random_state': 42,
                'n_jobs': -1
            }
            
            # Cross-validation
            cv_scores = []
            models = []
            
            for train_idx, val_idx in tscv.split(X):
                X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
                y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
                
                model = xgb.XGBClassifier(**params)
                model.fit(
                    X_train, y_train,
                    eval_set=[(X_val, y_val)],
                    verbose=False,
                    early_stopping_rounds=10
                )
                
                # Įvertinti
                accuracy = model.score(X_val, y_val)
                cv_scores.append(accuracy)
                models.append(model)
            
            # Pasirinkti geriausią modelį
            best_idx = np.argmax(cv_scores)
            self.model = models[best_idx]
            
            # Atnaujinti metadata
            self.model_metadata.update({
                'accuracy': float(np.mean(cv_scores)),
                'trained_samples': len(X),
                'last_trained': datetime.now().isoformat(),
                'cv_scores': [float(s) for s in cv_scores],
                'best_score': float(cv_scores[best_idx])
            })
            
            print(f"✅ Modelis apšviestas! Accuracy: {self.model_metadata['accuracy']:.2%}")
            print(f"   CV scores: {[f'{s:.2%}' for s in cv_scores]}")
            
            # Išsaugoti
            self.save_model()
            
            # Rodyti feature importance
            self.display_feature_importance()
            
            return True
            
        except Exception as e:
            print(f"❌ Klaida treniruojant modelį: {e}")
            return False
    
    def predict(self, features_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        Prognozuoti trade success tikimybę
        Returns: (probability, explanation_dict)
        """
        if self.model is None:
            # Grąžinti neutralią prognozę jei modelio nėra
            return 0.5, {'status': 'no_model', 'message': 'No ML model available'}
        
        try:
            # Užtikrinti feature tvarką
            if self.feature_names:
                features_df = features_df.reindex(columns=self.feature_names, fill_value=0)
            
            # Prognozė
            probability = self.model.predict_proba(features_df)[0][1]  # Class 1 probability
            
            # Gauti paaiškinimą
            explanation = self.explain_prediction(features_df)
            
            # Įrašyti į istoriją
            self.prediction_history.append({
                'timestamp': datetime.now().isoformat(),
                'probability': float(probability),
                'features': features_df.iloc[0].to_dict()
            })
            
            return float(probability), explanation
            
        except Exception as e:
            print(f"❌ Klaida prognozuojant: {e}")
            return 0.5, {'status': 'error', 'message': str(e)}
    
    def explain_prediction(self, features_df: pd.DataFrame) -> Dict:
        """Sukurti paaiškinimą kodėl tokia prognozė"""
        if self.model is None:
            return {}
        
        try:
            # Gauti feature importance
            importance = self.model.feature_importances_
            
            # Rikiuoti pagal svarbą
            sorted_idx = np.argsort(importance)[::-1]
            
            # Gauti top 5 features
            top_features = []
            for idx in sorted_idx[:5]:
                if idx < len(self.feature_names):
                    feat_name = self.feature_names[idx]
                    feat_value = features_df.iloc[0].get(feat_name, 0)
                    top_features.append({
                        'feature': feat_name,
                        'importance': float(importance[idx]),
                        'value': float(feat_value)
                    })
            
            # Apskaičiuoti confidence level
            prob = self.model.predict_proba(features_df)[0][1]
            if prob > 0.7:
                confidence_level = 'HIGH'
            elif prob > 0.55:
                confidence_level = 'MEDIUM'
            else:
                confidence_level = 'LOW'
            
            return {
                'top_features': top_features,
                'confidence_level': confidence_level,
                'model_accuracy': self.model_metadata['accuracy']
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def display_feature_importance(self, top_n: int = 10):
        """Rodyti svarbiausius features"""
        if self.model is None:
            return
        
        try:
            importance = self.model.feature_importances_
            feature_names = self.feature_names
            
            # Sudėti kartu ir surikiuoti
            feat_importance = list(zip(feature_names, importance))
            feat_importance.sort(key=lambda x: x[1], reverse=True)
            
            print("\n🏆 FEATURE IMPORTANCE (Top 10):")
            print("=" * 50)
            for i, (feat, imp) in enumerate(feat_importance[:top_n]):
                print(f"{i+1:2}. {feat:25} {imp:.4f}")
            print("=" * 50)
            
        except Exception as e:
            print(f"❌ Klaida rodant feature importance: {e}")

# ==================== TRADING ENHANCEMENT ENGINE ====================

class XGBoostTradingEngine:
    """
    Pagrindinė XGBoost integracijos klasė
    """
    
    def __init__(self, config: XGBoostConfig = None):
        self.config = config or XGBoostConfig()
        self.feature_engineer = FeatureEngineer()
        self.model_manager = XGBoostModelManager(self.config)
        self.data_collector = TradingDataCollector(self.config)
        
        # Performance tracking
        self.enhanced_signals = 0
        self.filtered_signals = 0
        self.total_signals = 0
        
        # Sukurti direktorijas
        self._create_directories()
        
        print(f"""
🤖 XGBoost Trading Engine Initialized
   Mode: {'SHADOW' if self.config.SHADOW_MODE else 'LIVE'}
   Confidence Boost: {'ENABLED' if self.config.CONFIDENCE_BOOST_ONLY else 'DISABLED'}
   Model Status: {'LOADED' if self.model_manager.model else 'NOT LOADED'}
        """)
    
    def _create_directories(self):
        """Sukurti reikalingas direktorijas"""
        directories = ['models', 'data', 'logs', 'reports']
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
    
    async def enhance_signal(self, original_signal: Dict, market_data: Dict) -> Dict:
        """
        Pagerinti signalą su XGBoost prognoze
        Returns: enhanced signal dict
        """
        self.total_signals += 1
        
        # Jei XGBoost išjungtas, grąžinti original signal
        if not self.config.ENABLED:
            return original_signal
        
        try:
            # ===== 1. FEATURE EXTRACTION =====
            features = self.feature_engineer.extract_features(original_signal, market_data)
            features_df = self.feature_engineer.prepare_feature_dataframe(features)
            
            # ===== 2. ML PREDICTION =====
            ml_probability, explanation = self.model_manager.predict(features_df)
            
            # ===== 3. ENHANCE SIGNAL =====
            enhanced_signal = original_signal.copy()
            
            # Add ML information
            enhanced_signal['ml_confidence'] = ml_probability
            enhanced_signal['ml_score'] = ml_probability * 100
            enhanced_signal['ml_explanation'] = explanation
            
            # Adjust final score (combine original and ML)
            original_score = enhanced_signal.get('score', 50)
            ml_score = ml_probability * 100
            
            if self.config.CONFIDENCE_BOOST_ONLY:
                # Tik confidence boosting - nesikeičia pagrindinis score
                enhanced_signal['final_score'] = original_score
                enhanced_signal['ml_adjusted_confidence'] = (
                    0.7 * ml_probability + 0.3 * enhanced_signal.get('confidence', 0.5)
                )
            else:
                # Pilna integracija - keičiamas score
                enhanced_signal['final_score'] = int(0.6 * original_score + 0.4 * ml_score)
                enhanced_signal['ml_adjusted_confidence'] = ml_probability
            
            # ===== 4. POSITION SIZE ADJUSTMENT =====
            if self.config.POSITION_ADJUSTMENT_ENABLED:
                base_position = enhanced_signal.get('position_size', 1.0)
                adjusted_position = self._adjust_position_size(base_position, ml_probability)
                enhanced_signal['recommended_position'] = adjusted_position
            
            # ===== 5. SIGNAL FILTERING =====
            should_trade = self._should_trade_based_on_ml(ml_probability, enhanced_signal)
            enhanced_signal['ml_trade_recommendation'] = should_trade
            
            if not should_trade:
                self.filtered_signals += 1
                enhanced_signal['trade_status'] = 'FILTERED_BY_ML'
            else:
                self.enhanced_signals += 1
                enhanced_signal['trade_status'] = 'APPROVED_BY_ML'
            
            # ===== 6. LOGGING =====
            if self.config.LOG_PREDICTIONS:
                self._log_prediction(original_signal, enhanced_signal, ml_probability)
            
            # ===== 7. SHADOW TRACKING =====
            if self.config.SHADOW_MODE:
                self.data_collector.record_shadow_signal(original_signal, enhanced_signal)
            
            return enhanced_signal
            
        except Exception as e:
            print(f"❌ Klaida enhancinant signalą: {e}")
            # Grąžinti original signal jei klaida
            original_signal['ml_error'] = str(e)
            return original_signal
    
    def _adjust_position_size(self, base_size: float, ml_probability: float) -> float:
        """Koreguoja pozicijos dydį pagal ML tikimybę"""
        if ml_probability > 0.75:
            # High confidence - increase position
            boost = min(self.config.MAX_POSITION_BOOST, 1.0 + (ml_probability - 0.75) * 2)
            return base_size * boost
        elif ml_probability < 0.45:
            # Low confidence - reduce position
            reduction = max(self.config.MIN_POSITION_REDUCTION, ml_probability * 2)
            return base_size * reduction
        else:
            # Medium confidence - keep as is
            return base_size
    
    def _should_trade_based_on_ml(self, ml_probability: float, signal: Dict) -> bool:
        """Nusprendžia ar trade'inti pagal ML prognozę"""
        # Tikriname ar pasiekėme minimalų slenkstį
        if ml_probability < self.config.CONFIDENCE_THRESHOLD:
            return False
        
        # Papildomi filtrai
        original_score = signal.get('score', 0)
        if original_score < 40:  # Per žemas originalus score
            return False
        
        # Patikrinti ar nėra per daug jau atidarytų pozicijų
        # (ši logika turėtų būti pagrindiniame bote)
        
        return True
    
    def _log_prediction(self, original_signal: Dict, enhanced_signal: Dict, ml_probability: float):
        """Įrašyti prognozę į log failą"""
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'asset': original_signal.get('asset'),
                'original_score': original_signal.get('score'),
                'ml_probability': ml_probability,
                'final_score': enhanced_signal.get('final_score'),
                'trade_status': enhanced_signal.get('trade_status'),
                'ml_recommendation': enhanced_signal.get('ml_trade_recommendation')
            }
            
            # Append į log failą
            log_file = self.config.LOG_PATH
            
            # Sukurti jei neegzistuoja
            if not os.path.exists(log_file):
                with open(log_file, 'w') as f:
                    json.dump([log_entry], f, indent=2)
            else:
                with open(log_file, 'r+') as f:
                    data = json.load(f)
                    data.append(log_entry)
                    f.seek(0)
                    json.dump(data, f, indent=2)
                    
        except Exception as e:
            print(f"❌ Klaida log'uojant: {e}")
    
    async def batch_enhance_signals(self, signals: List[Dict], market_data: Dict) -> List[Dict]:
        """Enhance'inti visus signalus vienu metu"""
        enhanced_signals = []
        
        for signal in signals:
            enhanced = await self.enhance_signal(signal, market_data)
            enhanced_signals.append(enhanced)
        
        return enhanced_signals
    
    def get_performance_stats(self) -> Dict:
        """Gauti ML modulio performance statistiką"""
        stats = {
            'total_signals': self.total_signals,
            'enhanced_signals': self.enhanced_signals,
            'filtered_signals': self.filtered_signals,
            'enhancement_rate': self.enhanced_signals / max(self.total_signals, 1),
            'filter_rate': self.filtered_signals / max(self.total_signals, 1),
            'model_status': 'loaded' if self.model_manager.model else 'not_loaded',
            'model_accuracy': self.model_manager.model_metadata.get('accuracy', 0),
            'shadow_mode': self.config.SHADOW_MODE
        }
        
        return stats
    
    def generate_report(self) -> str:
        """Sugeneruoti ataskaitą apie ML modulio veikimą"""
        stats = self.get_performance_stats()
        
        report = f"""
📊 XGBoost Enhancement Report
{'=' * 50}
📈 Performance Statistics:
   • Total Signals Processed: {stats['total_signals']}
   • Enhanced Signals: {stats['enhanced_signals']} ({stats['enhancement_rate']:.1%})
   • Filtered Signals: {stats['filtered_signals']} ({stats['filter_rate']:.1%})
   
🤖 Model Information:
   • Status: {stats['model_status'].upper()}
   • Accuracy: {stats['model_accuracy']:.2%}
   • Shadow Mode: {'ACTIVE' if stats['shadow_mode'] else 'INACTIVE'}
   
⚙️ Configuration:
   • Enabled: {self.config.ENABLED}
   • Confidence Threshold: {self.config.CONFIDENCE_THRESHOLD}
   • Min Training Samples: {self.config.MIN_TRAINING_SAMPLES}
   
💡 Recommendations:
"""
        
        if stats['model_accuracy'] < 0.6:
            report += "   ⚠️ Model accuracy is low. Consider retraining with more data.\n"
        
        if stats['enhancement_rate'] < 0.3:
            report += "   ℹ️ Low enhancement rate. ML is filtering most signals.\n"
        
        if not self.config.SHADOW_MODE:
            report += "   ✅ Shadow mode disabled - ML is affecting real trades.\n"
        
        report += "=" * 50
        
        return report

# ==================== DATA COLLECTOR ====================

class TradingDataCollector:
    """Rinkti duomenis ML treniravimui"""
    
    def __init__(self, config: XGBoostConfig):
        self.config = config
        self.training_data = []
        self.shadow_results = []
        
        # Įkelti esamus duomenis
        self.load_existing_data()
    
    def load_existing_data(self):
        """Įkelti esamus treniravimo duomenis"""
        try:
            if os.path.exists(self.config.DATA_PATH):
                with open(self.config.DATA_PATH, 'r') as f:
                    self.training_data = json.load(f)
                print(f"📂 Įkelta {len(self.training_data)} treniravimo įrašų")
        except Exception as e:
            print(f"❌ Klaida įkeliant duomenis: {e}")
            self.training_data = []
    
    def save_data(self):
        """Išsaugoti treniravimo duomenis"""
        try:
            with open(self.config.DATA_PATH, 'w') as f:
                json.dump(self.training_data, f, indent=2)
        except Exception as e:
            print(f"❌ Klaida saugant duomenis: {e}")
    
    def record_trade_result(self, signal_features: Dict, trade_result: Dict):
        """
        Įrašyti trade rezultatą treniravimui
        signal_features: features naudoti prognozei
        trade_result: {'profit_pct': float, 'success': bool, 'duration_minutes': int}
        """
        try:
            data_point = {
                'features': signal_features,
                'label': 1 if trade_result.get('success', False) else 0,
                'profit_pct': trade_result.get('profit_pct', 0),
                'duration': trade_result.get('duration_minutes', 0),
                'timestamp': datetime.now().isoformat(),
                'asset': trade_result.get('asset', 'UNKNOWN')
            }
            
            self.training_data.append(data_point)
            
            # Automatiškai išsaugoti kas 10 įrašų
            if len(self.training_data) % 10 == 0:
                self.save_data()
                print(f"💾 Išsaugoti {len(self.training_data)} treniravimo įrašai")
                
        except Exception as e:
            print(f"❌ Klaida įrašant trade result: {e}")
    
    def record_shadow_signal(self, original_signal: Dict, enhanced_signal: Dict):
        """Įrašyti shadow signalą palyginimui"""
        shadow_entry = {
            'timestamp': datetime.now().isoformat(),
            'original': original_signal,
            'enhanced': enhanced_signal,
            'ml_recommendation': enhanced_signal.get('ml_trade_recommendation', False)
        }
        
        self.shadow_results.append(shadow_entry)
        
        # Išsaugoti kas 50 įrašų
        if len(self.shadow_results) % 50 == 0:
            self.save_shadow_results()
    
    def save_shadow_results(self):
        """Išsaugoti shadow results"""
        try:
            def _json_safe(value):
                if isinstance(value, datetime):
                    return value.isoformat()
                if isinstance(value, (np.bool_, np.integer, np.floating)):
                    return value.item()
                if isinstance(value, np.ndarray):
                    return value.tolist()
                if isinstance(value, pd.Timestamp):
                    return value.isoformat()
                if is_dataclass(value):
                    return _json_safe(asdict(value))
                if isinstance(value, dict):
                    return {k: _json_safe(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [_json_safe(v) for v in value]
                if hasattr(value, "__dict__"):
                    return _json_safe(vars(value))
                return value

            shadow_path = self.config.DATA_PATH.replace('.json', '_shadow.json')
            with open(shadow_path, 'w') as f:
                safe_results = _json_safe(self.shadow_results[-1000:])  # Keep last 1000
                json.dump(safe_results, f, indent=2)
        except Exception as e:
            print(f"❌ Klaida saugant shadow results: {e}")
    
    def prepare_training_data(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Paruošti duomenis treniravimui"""
        if len(self.training_data) < self.config.MIN_TRAINING_SAMPLES:
            return pd.DataFrame(), pd.Series()
        
        # Konvertuoti į DataFrame
        X_data = []
        y_data = []
        
        for entry in self.training_data:
            try:
                features = entry['features']
                label = entry['label']
                
                # Konvertuoti į DataFrame row
                X_data.append(features)
                y_data.append(label)
            except Exception as e:
                continue
        
        if not X_data:
            return pd.DataFrame(), pd.Series()
        
        X_df = pd.DataFrame(X_data)
        y_series = pd.Series(y_data)
        
        # Užpildyti NaN reikšmes
        X_df = X_df.fillna(0)
        
        return X_df, y_series
    
    def get_data_stats(self) -> Dict:
        """Gauti duomenų statistiką"""
        wins = sum(1 for d in self.training_data if d.get('label') == 1)
        losses = len(self.training_data) - wins
        
        avg_profit = np.mean([d.get('profit_pct', 0) for d in self.training_data]) if self.training_data else 0.0

        return {
            'total_samples': len(self.training_data),
            'winning_trades': wins,
            'losing_trades': losses,
            'win_rate': wins / max(len(self.training_data), 1),
            'avg_profit_pct': avg_profit
        }
