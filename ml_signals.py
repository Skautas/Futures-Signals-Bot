"""
ML Signals Module
Machine learning predictor for signal quality
"""
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import pickle
import os
import json
from datetime import datetime


class MLPredictor:
    """ML model for signal prediction"""
    
    def __init__(self):
        self.is_trained = False
        self.accuracy = 0.0
        self.model = None
        self.scaler = StandardScaler()
        self.model_file = "ml_model.pkl"
        self.scaler_file = "ml_scaler.pkl"
        self.stats_file = "ml_stats.json"
        self.training_samples = 0
        self.labeled_samples = 0
        
        # Try to load existing model
        self._load_model()
    
    def _load_model(self):
        """Load saved model if exists"""
        try:
            if os.path.exists(self.model_file) and os.path.exists(self.scaler_file):
                with open(self.model_file, 'rb') as f:
                    self.model = pickle.load(f)
                with open(self.scaler_file, 'rb') as f:
                    self.scaler = pickle.load(f)
                self.is_trained = True
                
                # Load stats
                if os.path.exists(self.stats_file):
                    with open(self.stats_file, 'r') as f:
                        stats = json.load(f)
                        self.accuracy = stats.get('accuracy', 0.0)
                        self.training_samples = stats.get('training_samples', 0)
                        self.labeled_samples = stats.get('labeled_samples', 0)
        except Exception as e:
            print(f"ML Model load error: {e}")
            self.model = None
            self.is_trained = False
    
    def _save_model(self):
        """Save model to disk"""
        try:
            if self.model:
                with open(self.model_file, 'wb') as f:
                    pickle.dump(self.model, f)
                with open(self.scaler_file, 'wb') as f:
                    pickle.dump(self.scaler, f)
                
                stats = {
                    'accuracy': self.accuracy,
                    'training_samples': self.training_samples,
                    'labeled_samples': self.labeled_samples,
                    'last_trained': datetime.now().isoformat()
                }
                with open(self.stats_file, 'w') as f:
                    json.dump(stats, f)
        except Exception as e:
            print(f"ML Model save error: {e}")
    
    def _extract_features(self, signal_data: Dict) -> np.ndarray:
        """Extract features from signal data"""
        features = []
        
        # Basic features
        features.append(signal_data.get('score', 0))
        features.append(signal_data.get('rsi', 50))
        features.append(signal_data.get('adx', 0))
        
        # Direction encoding
        direction = signal_data.get('direction', 'NEUTRAL')
        features.append(1 if direction == 'LONG' else -1 if direction == 'SHORT' else 0)
        
        # Trend encoding
        trend = signal_data.get('trend', 'NEUTRAL')
        trend_map = {'STRONG_BULL': 2, 'BULL': 1, 'NEUTRAL': 0, 'BEAR': -1, 'STRONG_BEAR': -2}
        features.append(trend_map.get(trend, 0))
        
        # Signal count
        signals = signal_data.get('signals', [])
        features.append(len(signals) if isinstance(signals, list) else 0)
        
        # Additional features with defaults
        features.append(signal_data.get('macd_signal', 0))
        features.append(signal_data.get('stoch_signal', 0))
        features.append(signal_data.get('bb_signal', 0))
        features.append(signal_data.get('volume_ratio', 1.0))
        features.append(signal_data.get('volatility', 0))
        
        return np.array(features).reshape(1, -1)
    
    def predict_signal(self, signal_data: Dict) -> Optional[Dict]:
        """Predict signal confidence. Returns dict with 'confidence' key"""
        if not self.is_trained or self.model is None:
            # Return basic confidence based on score
            score = signal_data.get('score', 0)
            confidence = min(1.0, max(0.0, (score - 50) / 50.0))
            return {'confidence': confidence, 'model_used': False}
        
        try:
            # Extract features
            features = self._extract_features(signal_data)
            
            # Scale features
            features_scaled = self.scaler.transform(features)
            
            # Predict
            prediction = self.model.predict_proba(features_scaled)[0]
            
            # Confidence is the probability of the positive class
            confidence = float(prediction[1]) if len(prediction) > 1 else float(prediction[0])
            
            return {
                'confidence': confidence,
                'model_used': True,
                'prediction': 'GOOD' if confidence > 0.6 else 'POOR'
            }
        except Exception as e:
            print(f"ML Prediction error: {e}")
            # Fallback to score-based confidence
            score = signal_data.get('score', 0)
            confidence = min(1.0, max(0.0, (score - 50) / 50.0))
            return {'confidence': confidence, 'model_used': False, 'error': str(e)}
    
    def get_model_stats(self) -> Dict:
        """Get ML model statistics"""
        return {
            "is_trained": self.is_trained,
            "accuracy": self.accuracy,
            "training_samples": self.training_samples,
            "labeled_samples": self.labeled_samples,
            "model_exists": self.model is not None
        }
    
    def train(self, training_data: Optional[list] = None) -> Dict:
        """Train ML model"""
        # If no training data provided, create synthetic data for initial training
        if training_data is None or len(training_data) < 10:
            # Generate synthetic training data based on common patterns
            training_data = self._generate_synthetic_data()
        
        if len(training_data) < 10:
            return {
                "success": False,
                "message": f"Need at least 10 samples, got {len(training_data)}"
            }
        
        try:
            # Prepare features and labels
            X = []
            y = []
            
            for sample in training_data:
                features = self._extract_features(sample['features'])
                X.append(features[0])
                y.append(sample['label'])  # 1 for good signal, 0 for bad
            
            X = np.array(X)
            y = np.array(y)
            
            # Split data
            if len(X) > 20:
                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=0.2, random_state=42
                )
            else:
                X_train, y_train = X, y
                X_test, y_test = X, y
            
            # Scale features
            self.scaler.fit(X_train)
            X_train_scaled = self.scaler.transform(X_train)
            X_test_scaled = self.scaler.transform(X_test) if len(X_test) > 0 else X_train_scaled
            
            # Train model (use GradientBoosting for better performance)
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=5,
                random_state=42
            )
            self.model.fit(X_train_scaled, y_train)
            
            # Calculate accuracy
            if len(X_test) > 0:
                self.accuracy = float(self.model.score(X_test_scaled, y_test))
            else:
                self.accuracy = float(self.model.score(X_train_scaled, y_train))
            
            self.is_trained = True
            self.training_samples = len(X_train)
            self.labeled_samples = len(y)
            
            # Save model
            self._save_model()
            
            return {
                "success": True,
                "message": f"Model trained successfully",
                "accuracy": self.accuracy,
                "training_samples": self.training_samples,
                "test_samples": len(X_test) if len(X_test) > 0 else 0
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Training error: {str(e)}"
            }
    
    def _generate_synthetic_data(self) -> list:
        """Generate synthetic training data for initial model"""
        data = []
        
        # Good signals (high score, good indicators)
        for _ in range(20):
            data.append({
                'features': {
                    'score': np.random.randint(65, 100),
                    'rsi': np.random.randint(30, 70),
                    'adx': np.random.randint(25, 50),
                    'direction': 'LONG' if np.random.random() > 0.5 else 'SHORT',
                    'trend': np.random.choice(['BULL', 'STRONG_BULL', 'BEAR', 'STRONG_BEAR']),
                    'signals': ['SIGNAL1', 'SIGNAL2'],
                    'macd_signal': np.random.choice([-1, 0, 1]),
                    'stoch_signal': np.random.choice([-1, 0, 1]),
                    'bb_signal': np.random.choice([-1, 0, 1]),
                    'volume_ratio': np.random.uniform(0.8, 1.5),
                    'volatility': np.random.uniform(0.01, 0.05)
                },
                'label': 1  # Good signal
            })
        
        # Bad signals (low score, poor indicators)
        for _ in range(20):
            data.append({
                'features': {
                    'score': np.random.randint(40, 60),
                    'rsi': np.random.randint(20, 80),
                    'adx': np.random.randint(10, 25),
                    'direction': 'LONG' if np.random.random() > 0.5 else 'SHORT',
                    'trend': np.random.choice(['NEUTRAL', 'BULL', 'BEAR']),
                    'signals': ['SIGNAL1'],
                    'macd_signal': 0,
                    'stoch_signal': 0,
                    'bb_signal': 0,
                    'volume_ratio': np.random.uniform(0.5, 1.2),
                    'volatility': np.random.uniform(0.01, 0.1)
                },
                'label': 0  # Bad signal
            })
        
        return data


# Global instance
ml_predictor = MLPredictor()
