import logging
import os
from datetime import datetime

def setup_logger():
    """Setup logging to both console and file"""
    
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # Log filename with date
    log_filename = f"logs/expense_monitor_{datetime.now().strftime('%Y%m%d')}.log"
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler()  # Also print to console
        ]
    )
    
    logger = logging.getLogger('ExpenseMonitor')
    logger.info("="*70)
    logger.info("Logging initialized")
    logger.info(f"Log file: {log_filename}")
    logger.info("="*70)
    
    return logger
