"""
Telegram Bot Startup Script for Render (Webhook Mode)
"""
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == "__main__":
    print("🚀 Starting ExpenseFlow Telegram Bot (Webhook Mode)...")
    from src.telegram_bot import init_bot, start_flask_server
    
    # Initialize bot and set webhook
    init_bot()
    
    # Start Flask server to receive webhooks
    start_flask_server()
