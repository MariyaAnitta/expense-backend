"""
Telegram Bot Startup Script for Render (Webhook Mode)
Fixed for Gunicorn multi-threaded deployment
"""
import sys
import os
import logging

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add src directory to path if it exists
src_path = os.path.join(os.path.dirname(__file__), 'src')
if os.path.exists(src_path):
    sys.path.insert(0, src_path)
    from src.telegram_bot import app
else:
    # Import from current directory
    from src.telegram_bot import app

logger.info("🚀 ExpenseFlow Telegram Bot module loaded")

# The init_bot() function is called automatically when telegram_bot module loads
# This ensures bot is initialized before Gunicorn starts serving requests

# For local testing only (Gunicorn won't run this part)
if __name__ == "__main__":
    from src.telegram_bot import start_flask_server
    logger.info("🔧 Starting in local development mode...")
    start_flask_server()