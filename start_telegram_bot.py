"""
Telegram Bot Startup Script for Render (Webhook Mode)
"""
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import Flask app and initialization functions
from src.telegram_bot import app, init_bot, start_flask_server

# Initialize bot when module loads (CRITICAL for Gunicorn)
init_bot()

# For local testing only (Gunicorn won't run this part)
if __name__ == "__main__":
    print("🚀 Starting ExpenseFlow Telegram Bot (Webhook Mode)...")
    # Start Flask server to receive webhooks
    start_flask_server()
