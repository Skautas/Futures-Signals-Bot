"""
Sentiment Analyzer Module
Reddit sentiment analysis and Fear & Greed Index
"""
from typing import Dict, Any
import requests
import json
from datetime import datetime, timedelta
import time


class SentimentAnalyzer:
    """Sentiment analysis engine"""
    
    def __init__(self):
        self.fear_greed_cache = None
        self.fear_greed_cache_time = None
        self.cache_duration = 3600  # 1 hour cache
    
    def get_reddit_sentiment(self, asset_name: str) -> Dict:
        """Get Reddit sentiment for asset"""
        try:
            # Map asset names to Reddit subreddits
            subreddit_map = {
                'BTC': 'Bitcoin',
                'ETH': 'ethereum',
                'SOL': 'solana',
                'XRP': 'Ripple',
                'LTC': 'litecoin',
                'ADA': 'cardano',
                'DOT': 'polkadot'
            }
            
            subreddit = subreddit_map.get(asset_name, 'cryptocurrency')
            
            # Try to fetch from Reddit API (public, no auth needed for basic)
            try:
                url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=25"
                headers = {'User-Agent': 'TradingBot/1.0'}
                response = requests.get(url, headers=headers, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    posts = data.get('data', {}).get('children', [])
                    
                    # Analyze post titles and scores
                    sentiment_scores = []
                    posts_analyzed = 0
                    
                    for post in posts[:20]:  # Analyze top 20
                        post_data = post.get('data', {})
                        title = post_data.get('title', '').lower()
                        score = post_data.get('score', 0)
                        
                        # Simple sentiment keywords
                        bullish_words = ['bull', 'moon', 'pump', 'buy', 'long', 'rally', 'surge', 'breakout', 'up', 'rise']
                        bearish_words = ['bear', 'dump', 'sell', 'short', 'crash', 'drop', 'down', 'fall', 'correction']
                        
                        bullish_count = sum(1 for word in bullish_words if word in title)
                        bearish_count = sum(1 for word in bearish_words if word in title)
                        
                        # Weight by post score (higher score = more influence)
                        weight = min(score / 100.0, 1.0)  # Normalize to 0-1
                        
                        if bullish_count > bearish_count:
                            sentiment_scores.append(weight * 0.3)
                        elif bearish_count > bullish_count:
                            sentiment_scores.append(-weight * 0.3)
                        else:
                            sentiment_scores.append(0)
                        
                        posts_analyzed += 1
                    
                    # Calculate average sentiment
                    if sentiment_scores:
                        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
                    else:
                        avg_sentiment = 0.0
                    
                    # Normalize to -1 to 1 range
                    sentiment_score = max(-1.0, min(1.0, avg_sentiment))
                    
                    # Determine label
                    if sentiment_score > 0.1:
                        label = "BULLISH"
                    elif sentiment_score < -0.1:
                        label = "BEARISH"
                    else:
                        label = "NEUTRAL"
                    
                    return {
                        "sentiment_score": sentiment_score,
                        "sentiment_label": label,
                        "posts_analyzed": posts_analyzed,
                        "source": "Reddit"
                    }
            except Exception as e:
                print(f"Reddit API error: {e}")
        
        except Exception as e:
            print(f"Sentiment analysis error: {e}")
        
        # Fallback
        return {
            "sentiment_score": 0.0,
            "sentiment_label": "NEUTRAL",
            "posts_analyzed": 0,
            "source": "Reddit"
        }
    
    def get_fear_greed_index(self) -> Dict:
        """Get Crypto Fear & Greed Index"""
        try:
            # Check cache
            if self.fear_greed_cache and self.fear_greed_cache_time:
                if (datetime.now() - self.fear_greed_cache_time).seconds < self.cache_duration:
                    return self.fear_greed_cache
            
            # Try to fetch from alternative-c.me API (public endpoint)
            try:
                url = "https://api.alternative.me/fng/"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    if 'data' in data and len(data['data']) > 0:
                        fng_data = data['data'][0]
                        value = int(fng_data.get('value', 50))
                        
                        # Map to label
                        if value >= 75:
                            label = "EXTREME_GREED"
                        elif value >= 55:
                            label = "GREED"
                        elif value >= 45:
                            label = "NEUTRAL"
                        elif value >= 25:
                            label = "FEAR"
                        else:
                            label = "EXTREME_FEAR"
                        
                        result = {
                            "value": value,
                            "label": label
                        }
                        
                        # Cache result
                        self.fear_greed_cache = result
                        self.fear_greed_cache_time = datetime.now()
                        
                        return result
            except Exception as e:
                print(f"Fear & Greed API error: {e}")
        
        except Exception as e:
            print(f"Fear & Greed error: {e}")
        
        # Fallback
        return {
            "value": 50,
            "label": "NEUTRAL"
        }
    
    def get_all_sentiments(self) -> Dict:
        """Get sentiment for all assets"""
        fear_greed = self.get_fear_greed_index()
        
        return {
            "fear_greed": fear_greed
        }


# Global instance
sentiment_analyzer = SentimentAnalyzer()
