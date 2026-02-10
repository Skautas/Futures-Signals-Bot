"""
Greitas bear market integracijos testas
"""

import requests


def test_bear_status():
    """Testuoti /bear_status endpointa"""
    print("🔍 Testing Bear Market Integration")
    print("=" * 50)
    
    try:
        # Test 1: Bear status endpoint
        response = requests.get("http://localhost:5000/bear_status", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ /bear_status endpoint WORKS")
            print(f"   Market mode: {data.get('market_mode')}")
            print(f"   Bear strength: {data.get('bear_strength', 0)}%")
            print(f"   Position size: {data.get('risk', {}).get('position_size', 'N/A')}")
        else:
            print(f"❌ /bear_status failed: {response.status_code}")
            
    except requests.ConnectionError:
        print("❌ Cannot connect to Flask server")
        print("   Make sure bot is running: python futures_signals.py")
    except Exception as e:
        print(f"❌ Error: {e}")


def test_bear_logic():
    """Testuoti bear market logika"""
    print("\n🧠 Testing Bear Market Logic")
    print("=" * 50)
    
    # Test scenarios
    test_cases = [
        {
            "name": "SHORT in bear market",
            "direction": "SHORT",
            "rsi": 35,
            "body_pct": 0.8,
            "atr_pct": 2.2,
            "expected": "ALLOWED"
        },
        {
            "name": "LONG with high RSI",
            "direction": "LONG",
            "rsi": 82,
            "body_pct": 0.5,
            "atr_pct": 1.5,
            "expected": "BLOCKED (RSI > 80)"
        },
        {
            "name": "Large candle body",
            "direction": "SHORT",
            "rsi": 40,
            "body_pct": 1.3,
            "atr_pct": 2.0,
            "expected": "BLOCKED (body > 1.2%)"
        }
    ]
    
    for test in test_cases:
        print(f"\n📊 {test['name']}:")
        print(f"   Direction: {test['direction']}")
        print(f"   RSI: {test['rsi']} (limit: {80 if test['direction'] == 'LONG' else 25})")
        print(f"   Body: {test['body_pct']}% (limit: 1.2%)")
        print(f"   ATR: {test['atr_pct']}% (limit: 2.8%)")
        print(f"   Expected: {test['expected']}")


def check_bot_logs_for_bear():
    """Patikrinti ar botas rodo bear market zinutes"""
    print("\n📝 Checking for Bear Market messages in logs")
    print("=" * 50)
    
    messages_to_check = [
        "Bear Market Engine loaded",
        "BEAR MARKET ENGINE INITIALIZED",
        "BEAR MARKET DETECTED",
        "BEAR MARKET SIGNAL:",
        "bear market filters blocked"
    ]
    
    print("Look for these messages in bot logs:")
    for msg in messages_to_check:
        print(f"   • {msg}")


if __name__ == "__main__":
    test_bear_status()
    test_bear_logic()
    check_bot_logs_for_bear()
    
    print("\n🎯 NEXT STEPS:")
    print("1. Run bot and check logs for bear market messages")
    print("2. Test /bear_status endpoint")
    print("3. Monitor if signals are now passing filters")
    print("4. Check position sizing (should be 70% normal)")
