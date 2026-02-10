def send_signal(asset, direction, confidence, reason):
    message = f"""
🚨 SIGNAL: {asset}
Direction: {direction}
Confidence: {confidence}
Reason: {reason}
"""
    print(message)
