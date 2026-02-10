"""
Main bot entry point - imports and runs the trading bot
"""
import sys
import os

# Add parent directory to path to import futures_signals
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import and run the main bot
from futures_signals import main

if __name__ == "__main__":
    main()

