"""
Telegram Bot Startup Script for Render (Webhook Mode)
Fixed for Gunicorn multi-threaded deployment
"""
import sys
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import Flask app from src.telegram_bot
from src.telegram_bot import app

logger.info("🚀 ExpenseFlow Telegram Bot module loaded")

# The init_bot() is called automatically when telegram_bot module imports
# This ensures bot is ready before Gunicorn serves requests

# For local testing only (Gunicorn won't run this)
if __name__ == "__main__":
    from src.telegram_bot import start_flask_server
    logger.info("🔧 Starting in local development mode...")
    start_flask_server()