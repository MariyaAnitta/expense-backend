"""
Telegram Bot Startup Script for Render Deployment
Runs the ExpenseFlow Telegram bot continuously
"""
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import and run (this will work at runtime despite Pylance warning)
if __name__ == "__main__":
    print("🚀 Starting ExpenseFlow Telegram Bot on Render...")
    from src.telegram_bot import start_telegram_bot
    start_telegram_bot()
